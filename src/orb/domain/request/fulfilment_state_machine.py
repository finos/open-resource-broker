"""Fulfilment state machine — the single write authority for request status.

Every status transition in the system routes through :class:`FulfilmentStateMachine`.
It is a stateless, injectable domain service: it holds no repositories and no
mutable state beyond an injected grace period.  It takes a ``Request`` plus a
semantic ``FulfilmentEvent`` and returns a NEW ``Request`` with the transition
applied (or raises ``InvalidRequestStateError`` for a genuinely illegal move).

Why a service and not more aggregate methods: the transition rules now depend on
wall-clock time (``deadline_at``) and on a config value (the grace period). A
pure aggregate method cannot own those without a clock/config leak. The state
machine is the DDD "policy" object; the aggregate stays a pure data + invariant
holder whose raw ``_transition`` mutator is driven exclusively from here.

Pure domain — no infra imports.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

from orb.domain.base.clock import Clock, SystemClock
from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.request.deadline_release import DEADLINE_RELEASE_KEY, RELEASE_PENDING
from orb.domain.request.exceptions import InvalidRequestStateError
from orb.domain.request.fulfillment_config import merged_fulfillment_config
from orb.domain.request.request_types import RequestStatus, RequestType

logger = logging.getLogger(__name__)

# Settings used when the state machine is built without configured defaults. They
# match the configuration defaults for provisioning and dispatch timeouts.
_BUILTIN_FULFILMENT_DEFAULTS: dict[str, Any] = {
    "timeout_seconds": 300,
    "dispatch_timeout_seconds": 300,
}

# Margin added to the longest a live create can run before it persists its
# resource ids, so a request still being created is never judged abandoned.
_ABANDON_MARGIN_SECONDS = 300

_ABANDONED_MESSAGE = "Request abandoned before provisioning started"

# Without a fresh provider read an expired request that already has machine ids
# waits this long past its deadline (or its grace period when longer) before it is
# finalised from what is stored, so a poll or reconcile pass can decide it first.
_BACKSTOP_FLOOR = timedelta(minutes=10)


class FulfilmentEvent(str, Enum):
    """Semantic events that drive a request through its fulfilment lifecycle."""

    START = "start"  # PENDING -> IN_PROGRESS
    RESOURCES_CREATED = "resources_created"  # -> ACQUIRING
    PROVIDER_VERDICT = "provider_verdict"  # carries a ProviderFulfilment
    CANCEL = "cancel"
    FAIL = "fail"
    DEADLINE_SWEEP = "deadline_sweep"  # lazy deadline check, no external target


# Map a ProviderFulfilment.state to a target RequestStatus. ``partial`` is
# resolved separately (deadline-dependent) — see ``_target_for_verdict``.
_VERDICT_STATUS: dict[str, RequestStatus] = {
    "fulfilled": RequestStatus.COMPLETED,
    "in_progress": RequestStatus.IN_PROGRESS,
    "failed": RequestStatus.FAILED,
}


class FulfilmentStateMachine:
    """Single entry point for every request status write."""

    def __init__(
        self,
        grace_period_seconds: int,
        fulfilment_defaults: Optional[Mapping[str, Any]] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        """Create the state machine.

        Args:
            grace_period_seconds: How long a request may take before its deadline.
            fulfilment_defaults: Configured provisioning settings
                (``timeout_seconds``, ``dispatch_timeout_seconds``) that a request's
                own ``fulfillment_config`` metadata may override.
            clock: Time source; defaults to the system clock.
        """
        self._grace_period = timedelta(seconds=max(0, int(grace_period_seconds)))
        self._clock: Clock = clock or SystemClock()
        defaults = {**_BUILTIN_FULFILMENT_DEFAULTS, **(fulfilment_defaults or {})}
        if self._timeouts(defaults) is None:
            defaults = dict(_BUILTIN_FULFILMENT_DEFAULTS)
        self._fulfilment_defaults = defaults

    def now(self) -> datetime:
        """Current time from the machine's clock, for callers that pass ``now=``."""
        return self._clock.now()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(
        self,
        request: Request,
        event: FulfilmentEvent,
        *,
        now: datetime,
        fulfilment: Optional[ProviderFulfilment] = None,
        diagnostic: Optional[FulfilmentDiagnostic] = None,
        message: Optional[str] = None,
        reason: Optional[str] = None,
        running_count: Optional[int] = None,
        fresh_read: bool = False,
    ) -> Request:
        """Apply a semantic event and return the resulting Request.

        ``running_count`` and ``fresh_read`` describe the provider read the caller
        just made; they only matter when the deadline has passed (see
        :meth:`evaluate_deadline`).

        Raises ``InvalidRequestStateError`` only for genuinely illegal
        transitions — never for an idempotent re-application of the current
        state.
        """
        # 1. Lazy deadline first — but CANCEL / FAIL carry operator / hard-failure
        #    authority and override an expired deadline, so the sweep is skipped
        #    for them (a cancel on an about-to-timeout request must still cancel).
        #    For every other event: if the sweep *itself* flips the request to a
        #    terminal state, that outcome wins over a late provider poll (the
        #    request already timed out). An already-terminal request is NOT
        #    short-circuited — it falls through to normal validation so the
        #    back-compat PARTIAL→COMPLETED upgrade still works and genuinely
        #    illegal transitions still raise.
        if event not in (FulfilmentEvent.CANCEL, FulfilmentEvent.FAIL):
            swept = self.evaluate_deadline(
                request, now=now, running_count=running_count, fresh_read=fresh_read
            )
            deadline_flipped = swept is not request
            if deadline_flipped and swept.status.is_terminal():
                return swept
            request = swept

        if event == FulfilmentEvent.DEADLINE_SWEEP:
            # Deadline already evaluated above; nothing else to do.
            return request

        # 2. Resolve the target status for this event.
        target, resolved_message, resolved_diag = self._resolve_target(
            request,
            event,
            now=now,
            fulfilment=fulfilment,
            diagnostic=diagnostic,
            message=message,
            reason=reason,
        )

        # 3. Idempotent re-application is a no-op (but still merge a diagnostic
        #    when one was supplied so late error detail is never dropped).
        if target == request.status:
            if resolved_diag is not None:
                return request.set_fulfilment_diagnostic(resolved_diag)
            return request

        # 4. Terminal guard + transition-table validation.
        if not request.status.can_transition_to(target):
            raise InvalidRequestStateError(request.status.value, target.value)

        # 5. deadline_at is set once, when the request first leaves PENDING.
        deadline_at: Optional[datetime] = None
        if request.deadline_at is None and target != RequestStatus.PENDING:
            base = request.started_at or now
            deadline_at = base + self._grace_period

        # A clean success terminal (COMPLETED) with no diagnostic supplied must
        # clear any stale shortfall diagnostic carried on the row from an earlier
        # PARTIAL_PENDING poll — a fully-completed request has nothing to explain.
        clear_diagnostic = target == RequestStatus.COMPLETED and resolved_diag is None

        return request._transition(
            target,
            now=now,
            message=resolved_message,
            diagnostic=resolved_diag,
            deadline_at=deadline_at,
            clear_diagnostic=clear_diagnostic,
        )

    def evaluate_deadline(
        self,
        request: Request,
        *,
        now: datetime,
        running_count: Optional[int] = None,
        fresh_read: bool = False,
    ) -> Request:
        """Lazily resolve an expired request to a terminal outcome.

        Idempotent and safe on read paths: returns the request unchanged when
        it is already terminal, has no deadline, or the deadline has not passed.

        The outcome compares the number of machines actually running with the
        requested count:

          - COMPLETED (no DEADLINE diagnostic) when the running count reaches the
            requested count.
          - PARTIAL  (DEADLINE diagnostic) when some, but not all, are running.
          - TIMEOUT  (DEADLINE diagnostic) when none are running.

        Where the running count comes from:

          - ``running_count`` given (with ``fresh_read``): the caller counted the
            running machines in a provider read it just made.
          - Not given: the count is unknown, never zero. With ``fresh_read`` or
            without machine ids the older rule applies,
            ``max(successful_count, len(machine_ids))``, so a provider that never
            reports a count keeps its outcome. Without a fresh read and with
            machine ids the outcome waits for a read to decide it, until the
            backstop (deadline plus the longer of the grace period and ten
            minutes), when the count stored from the last read is used. A stored
            zero with machine ids gives PARTIAL, because TIMEOUT cannot be
            upgraded later.

        A PARTIAL or TIMEOUT outcome that has machine ids is stamped in the same
        transition as owing release of machines that may never have started.
        """
        if request.status.is_terminal():
            return request
        # Deadline/holding semantics are an ACQUIRE-only concern. RETURN requests
        # reach terminal PARTIAL directly (a return that partially completed is a
        # settled outcome, not a capacity-holding state), so they are never swept
        # by the deadline machinery.
        if request.request_type != RequestType.ACQUIRE:
            return request
        if request.status == RequestStatus.PENDING:
            return self._fail_if_abandoned(request, now=now)
        if request.deadline_at is None or now < request.deadline_at:
            return request
        if request.status not in (
            RequestStatus.IN_PROGRESS,
            RequestStatus.ACQUIRING,
            RequestStatus.PARTIAL_PENDING,
        ):
            return request

        # Older rule: the healed successful_count OR the populated machine_ids,
        # whichever is larger. Used when no running count is known.
        legacy_count = max(request.successful_count, len(request.machine_ids))
        has_machine_ids = bool(request.machine_ids)

        effective_count: int
        partial_floor = False
        if running_count is not None:
            effective_count = running_count
        elif fresh_read or not has_machine_ids:
            effective_count = legacy_count
        else:
            if now < request.deadline_at + max(self._grace_period, _BACKSTOP_FLOOR):
                return request
            stored = self._stored_running_count(request)
            if stored is None:
                effective_count = legacy_count
            elif stored == 0:
                # Stored zero is weak evidence (providers report 0 while a lookup
                # is still failing). With ids present call it PARTIAL.
                effective_count = 0
                partial_floor = True
            else:
                effective_count = stored

        diagnostic: Optional[FulfilmentDiagnostic]
        if request.requested_count > 0 and effective_count >= request.requested_count:
            # Fully fulfilled but never received its ``fulfilled`` verdict before
            # the deadline — complete it, do not downgrade to PARTIAL.  No DEADLINE
            # diagnostic: nothing fell short.
            target = RequestStatus.COMPLETED
            summary = f"Fully fulfilled: {effective_count}/{request.requested_count} at deadline"
            diagnostic = None
        elif effective_count > 0 or partial_floor:
            target = RequestStatus.PARTIAL
            summary = f"Deadline exceeded: {effective_count}/{request.requested_count} fulfilled"
            diagnostic = FulfilmentDiagnostic(
                category=DiagnosticCategory.DEADLINE,
                summary=summary,
                occurred_at=now,
            )
        else:
            target = RequestStatus.TIMEOUT
            summary = "Deadline exceeded before any capacity was fulfilled"
            diagnostic = FulfilmentDiagnostic(
                category=DiagnosticCategory.DEADLINE,
                summary=summary,
                occurred_at=now,
            )

        if not request.status.can_transition_to(target):
            # Should not happen given the transition table, but never raise on a
            # read path — leave the request untouched.
            return request
        # A deadline sweep that resolves to COMPLETED (capacity was actually met,
        # just never got its 'fulfilled' verdict) has no diagnostic — clear any
        # stale shortfall diagnostic so the completed request stays clean.
        clear_diagnostic = target == RequestStatus.COMPLETED and diagnostic is None
        # A request with only a resource id may have launched machines the
        # request never recorded, so it owes a release as well.
        owes_release = target in (RequestStatus.PARTIAL, RequestStatus.TIMEOUT) and (
            has_machine_ids or bool(request.resource_ids)
        )
        return request._transition(
            target,
            now=now,
            message=summary,
            diagnostic=diagnostic,
            clear_diagnostic=clear_diagnostic,
            metadata_updates={DEADLINE_RELEASE_KEY: RELEASE_PENDING} if owes_release else None,
        )

    @staticmethod
    def _stored_running_count(request: Request) -> Optional[int]:
        """The running count stored from the last provider read, None when unknown."""
        snapshot = request.metadata.get("last_fulfilment")
        if not isinstance(snapshot, Mapping):
            return None
        count = snapshot.get("running_count")
        if isinstance(count, bool) or not isinstance(count, int):
            return None
        return count

    def abandon_after(self, request: Request) -> timedelta:
        """How long a request may stay PENDING before it counts as abandoned.

        A live create runs its elapsed check only when an attempt starts, so it
        lasts at most the provisioning timeout plus one dispatch timeout, followed
        by persistence. Settings come from the same merge provisioning uses, so a
        per-request override widens this bound too. Never shorter than the grace
        period. Unusable overrides fall back to the configured defaults.
        """
        timeouts: Optional[tuple[float, float]] = None
        try:
            timeouts = self._timeouts(merged_fulfillment_config(self._fulfilment_defaults, request))
        except Exception as exc:
            logger.warning("Unusable fulfillment_config on request %s: %s", request.request_id, exc)
        if timeouts is None:
            logger.warning(
                "Using the configured fulfilment timeouts for request %s", request.request_id
            )
            timeouts = self._timeouts(self._fulfilment_defaults) or (300.0, 300.0)
        timeout_seconds, dispatch_timeout_seconds = timeouts
        longest_create = timeout_seconds + dispatch_timeout_seconds + _ABANDON_MARGIN_SECONDS
        return max(self._grace_period, timedelta(seconds=longest_create))

    @staticmethod
    def _timeouts(config: Mapping[str, Any]) -> Optional[tuple[float, float]]:
        """Return (timeout_seconds, dispatch_timeout_seconds), or None if unusable."""
        try:
            timeout_seconds = float(config["timeout_seconds"])
            dispatch_timeout_seconds = float(config["dispatch_timeout_seconds"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (math.isfinite(timeout_seconds) and math.isfinite(dispatch_timeout_seconds)):
            return None
        # Bound the sum so it always fits in a timedelta.
        timeout_seconds = min(max(timeout_seconds, 0.0), 1e9)
        dispatch_timeout_seconds = min(max(dispatch_timeout_seconds, 0.0), 1e9)
        return timeout_seconds, dispatch_timeout_seconds

    def _fail_if_abandoned(self, request: Request, *, now: datetime) -> Request:
        """Fail a PENDING request whose create flow never recorded anything.

        Only a request still awaiting resources can be abandoned: one that has
        resource or machine ids is owned by the status sync, not by a create.
        Never raises on a read path.
        """
        if not request.awaiting_resources:
            return request
        created_at = request.created_at
        if created_at is None:
            return request
        try:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=now.tzinfo)
            if now - created_at < self.abandon_after(request):
                return request
            return request._transition(
                RequestStatus.FAILED,
                now=now,
                message=_ABANDONED_MESSAGE,
                diagnostic=FulfilmentDiagnostic(
                    category=DiagnosticCategory.INTERNAL,
                    summary=_ABANDONED_MESSAGE,
                    occurred_at=now,
                ),
            )
        except Exception as exc:
            logger.debug("Abandoned-request check skipped for %s: %s", request.request_id, exc)
            return request

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_target(
        self,
        request: Request,
        event: FulfilmentEvent,
        *,
        now: datetime,
        fulfilment: Optional[ProviderFulfilment],
        diagnostic: Optional[FulfilmentDiagnostic],
        message: Optional[str],
        reason: Optional[str],
    ) -> tuple[RequestStatus, Optional[str], Optional[FulfilmentDiagnostic]]:
        """Map an event to (target_status, message, diagnostic)."""
        if event == FulfilmentEvent.START:
            return RequestStatus.IN_PROGRESS, message, diagnostic

        if event == FulfilmentEvent.RESOURCES_CREATED:
            return (
                RequestStatus.ACQUIRING,
                message or "Provider resources created, waiting for completion",
                diagnostic,
            )

        if event == FulfilmentEvent.CANCEL:
            cancel_diag = diagnostic or FulfilmentDiagnostic(
                category=DiagnosticCategory.CANCELLED,
                summary=reason or "Request cancelled",
                occurred_at=now,
            )
            return RequestStatus.CANCELLED, reason or message, cancel_diag

        if event == FulfilmentEvent.FAIL:
            return RequestStatus.FAILED, message, diagnostic

        if event == FulfilmentEvent.PROVIDER_VERDICT:
            if fulfilment is None:
                raise ValueError("PROVIDER_VERDICT event requires a ProviderFulfilment")
            return self._target_for_verdict(
                request, fulfilment, now=now, message=message, diagnostic=diagnostic
            )

        raise ValueError(f"Unhandled fulfilment event: {event}")

    def _target_for_verdict(
        self,
        request: Request,
        fulfilment: ProviderFulfilment,
        *,
        now: datetime,
        message: Optional[str],
        diagnostic: Optional[FulfilmentDiagnostic],
    ) -> tuple[RequestStatus, Optional[str], Optional[FulfilmentDiagnostic]]:
        """Resolve a provider capacity verdict to a target status.

        The core new rule lives here: a *still-gathering* ``partial`` verdict
        maps to the non-terminal PARTIAL_PENDING holding state while the request
        is still within its deadline (a later poll can still complete it), and
        to terminal PARTIAL once the deadline has passed.

        A *final* partial verdict — one the provider explicitly marks as its
        last word (``fulfilment.final is True``) — resolves to terminal PARTIAL
        immediately even within the deadline.  Holding a settled shortfall in
        PARTIAL_PENDING would waste the whole grace period waiting for a
        ``fulfilled`` verdict that can never arrive.  Only synchronous providers
        (AWS RunInstances / instant fleet / MicroVM) set ``final=True``.

        Finality is NOT inferred from ``pending_count``.  For asynchronous
        providers such as Kubernetes every ``partial`` verdict is emitted only
        when ``pending_count == 0`` (the resolvers return ``in_progress`` while
        pods are still pending), yet that zero is *transient* — the pod list
        lags the controller's reconciliation intent (e.g. a StatefulSet
        OrderedReady rollout between pod-N Ready and pod-(N+1) creation shows
        N ready / 0 pending for a moment).  Treating ``pending_count == 0`` as
        settled would strand such requests in a terminal PARTIAL that the
        recovery sweep never re-syncs.  So a non-final partial always parks in
        PARTIAL_PENDING within the deadline, letting the poll/cache heal it.
        """
        # Prefer an explicit diagnostic arg; otherwise carry the one the
        # provider attached to its verdict.
        resolved_diag = diagnostic or fulfilment.diagnostic
        resolved_message = message if message is not None else fulfilment.message

        if fulfilment.state == "partial":
            # The PARTIAL_PENDING holding state is an ACQUIRE-only concern:
            # while an acquire is within its deadline a partial verdict is a
            # transient "still gathering capacity" signal. A RETURN request has
            # no such holding semantics — a partial return is a settled terminal
            # outcome, so it goes straight to terminal PARTIAL.
            if request.request_type != RequestType.ACQUIRE:
                return RequestStatus.PARTIAL, resolved_message, resolved_diag
            # A provider-declared final partial is a settled shortfall: go
            # straight to terminal PARTIAL rather than parking it in the holding
            # state to burn the grace period on a completion that can never
            # come.  Only synchronous providers (AWS launch APIs) set
            # ``final=True``; asynchronous providers (Kubernetes) leave it False
            # so a transient partial can still heal.  Finality is never inferred
            # from ``pending_count`` — see the docstring for why that is unsafe.
            if fulfilment.final:
                return RequestStatus.PARTIAL, resolved_message, resolved_diag
            within_deadline = request.deadline_at is None or now < request.deadline_at
            target = RequestStatus.PARTIAL_PENDING if within_deadline else RequestStatus.PARTIAL
            return target, resolved_message, resolved_diag

        target = _VERDICT_STATUS.get(fulfilment.state)
        if target is None:
            # Unknown verdict — stay in progress rather than guess a terminal.
            return RequestStatus.IN_PROGRESS, resolved_message, resolved_diag
        return target, resolved_message, resolved_diag

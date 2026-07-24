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

from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.request.exceptions import InvalidRequestStateError
from orb.domain.request.request_types import RequestStatus, RequestType


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

    def __init__(self, grace_period_seconds: int) -> None:
        self._grace_period = timedelta(seconds=max(0, int(grace_period_seconds)))

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
    ) -> Request:
        """Apply a semantic event and return the resulting Request.

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
            swept = self.evaluate_deadline(request, now=now)
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

    def evaluate_deadline(self, request: Request, *, now: datetime) -> Request:
        """Lazily resolve an expired request to a terminal outcome.

        Idempotent and safe on read paths: returns the request unchanged when
        it is already terminal, has no deadline, or the deadline has not passed.
        An expired non-terminal request is classified against
        ``effective_count = max(successful_count, len(machine_ids))`` — the same
        authoritative capacity signal used elsewhere.  ``successful_count`` alone
        lags (it is not healed for ACQUIRING), so a request whose machine_ids are
        already populated must not be judged solely by the stale counter.

          - COMPLETED (no DEADLINE diagnostic — the capacity was actually met)
            when ``requested_count > 0`` and ``effective_count >= requested_count``.
            A fully-fulfilled request that merely missed its ``fulfilled`` verdict
            before the deadline must not be downgraded to a failure-like PARTIAL.
          - PARTIAL  (DEADLINE diagnostic) when ``0 < effective_count < requested_count``.
          - TIMEOUT  (DEADLINE diagnostic) when ``effective_count == 0``.
        """
        if request.status.is_terminal():
            return request
        # Deadline/holding semantics are an ACQUIRE-only concern. RETURN requests
        # reach terminal PARTIAL directly (a return that partially completed is a
        # settled outcome, not a capacity-holding state), so they are never swept
        # by the deadline machinery.
        if request.request_type != RequestType.ACQUIRE:
            return request
        if request.deadline_at is None or now < request.deadline_at:
            return request
        if request.status not in (
            RequestStatus.IN_PROGRESS,
            RequestStatus.ACQUIRING,
            RequestStatus.PARTIAL_PENDING,
        ):
            return request

        # Authoritative capacity: the healed successful_count OR the populated
        # machine_ids, whichever is larger.  machine_ids is populated even in
        # ACQUIRING (where successful_count is not yet reconciled), so relying on
        # successful_count alone would misclassify a request that already has its
        # instances.
        effective_count = max(request.successful_count, len(request.machine_ids))

        diagnostic: Optional[FulfilmentDiagnostic]
        if request.requested_count > 0 and effective_count >= request.requested_count:
            # Fully fulfilled but never received its ``fulfilled`` verdict before
            # the deadline — complete it, do not downgrade to PARTIAL.  No DEADLINE
            # diagnostic: nothing fell short.
            target = RequestStatus.COMPLETED
            summary = f"Fully fulfilled: {effective_count}/{request.requested_count} at deadline"
            diagnostic = None
        elif effective_count > 0:
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
        return request._transition(
            target,
            now=now,
            message=summary,
            diagnostic=diagnostic,
            clear_diagnostic=clear_diagnostic,
        )

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

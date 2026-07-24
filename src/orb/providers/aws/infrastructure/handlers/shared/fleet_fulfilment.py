"""Shared fleet fulfilment computation helpers.

Both EC2 Fleet (Maintain/Request types) and Spot Fleet share identical
capacity-based fulfilment semantics: FulfilledCapacity >= TargetCapacity AND
no pending or failed instances → fulfilled.  The only difference is the label
used in human-readable messages.

``compute_ec2fleet_fulfilment`` handles the full EC2 Fleet decision tree,
dispatching to ``compute_capacity_based_fulfilment`` for Maintain/Request fleet
types and using count-based logic for Instant fleets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from orb.domain.base.provider_fulfilment import FulfilmentState
    from orb.providers.aws.domain.template.value_objects import AWSFleetType

from orb.domain.base.provider_fulfilment import ProviderFulfilment


def _sum_optional_counts(values: list[Optional[int]]) -> Optional[int]:
    """Sum a list of optional capacity counts, preserving 'unknown' vs 'zero'.

    A ``None`` contributor is treated as 0 for summation, but if *every*
    contributor is ``None`` the result is ``None`` — collapsing an all-unknown
    field to 0 would fabricate a definite 'zero' the providers never reported.
    """
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present)


# The combined states for which a merged diagnostic is meaningful.  A
# diagnostic exists to explain a *shortfall* or *failure*; an ``in_progress``
# aggregate (a fleet is still booting — nothing has gone wrong yet) and a
# ``fulfilled`` aggregate (nothing to explain) must carry ``diagnostic=None``,
# exactly like the single-fleet path never attaches a diagnostic to those two
# states.  Gating here keeps the multi-fleet contract symmetric with the
# single-fleet one and stops a transient IN_PROGRESS poll from being stamped
# with (and re-persisting every poll) a contributor's CAPACITY shortfall.
_DIAGNOSTIC_STATES: frozenset[str] = frozenset({"partial", "failed"})


def aggregate_fleet_fulfilment(
    state: "FulfilmentState",
    message: str,
    final: bool,
    contributors: list[ProviderFulfilment],
    requested_count: int,
) -> ProviderFulfilment:
    """Build the combined verdict for a multi-fleet request.

    A multi-fleet request's overall verdict must carry the SAME contract as the
    single-fleet path — not just ``state``/``message``/``final``. This helper
    propagates every meaningful field so a multi-fleet partial stamps the
    request DTO with a diagnostic and capacity numbers exactly like a
    single-fleet partial would.

    ``target_units`` is the whole-request target — ``requested_count`` — a
    single known value, NOT the sum of the contributors' ``target_units``.  The
    per-fleet handlers fall back to the full ``request.requested_count`` when
    AWS omits a fleet's capacity (a not-yet-visible fleet, or one missing
    ``TargetCapacity``), so each such fleet already stamps ``target_units`` with
    the whole-request total; summing those fallbacks over-counts the target
    (a 10-instance request split across two fleets could report 15).  Because
    ORB always submits ``TotalTargetCapacity == requested_count`` (see the fleet
    config builders), ``requested_count`` is exactly the whole-request target
    on the same capacity-unit scale as the summed ``fulfilled_units``.

    The observed counts (``fulfilled_units``/``running_count``/``pending_count``/
    ``failed_count``) ARE summed across all contributing fleets — those are real
    per-fleet observations, not request-total fallbacks — so the aggregate
    represents the whole request's observed capacity; each stays ``None`` only
    when every contributor reported ``None`` (see :func:`_sum_optional_counts`).

    ``diagnostic`` is the most-severe merge (:meth:`FulfilmentDiagnostic.merge`)
    of every contributor's diagnostic — but ONLY when ``state`` is a genuine
    shortfall/failure (``partial``/``failed``; see ``_DIAGNOSTIC_STATES``).  An
    ``in_progress`` or ``fulfilled`` aggregate carries ``diagnostic=None``,
    matching the single-fleet path, since neither has anything to explain.
    ``None`` too when no contributor has a diagnostic to merge.
    """
    from orb.domain.base.diagnostic import FulfilmentDiagnostic

    merged_diagnostic: Optional[FulfilmentDiagnostic] = None
    if state in _DIAGNOSTIC_STATES:
        for contributor in contributors:
            diag = contributor.diagnostic
            if diag is None:
                continue
            merged_diagnostic = (
                diag
                if merged_diagnostic is None
                else FulfilmentDiagnostic.merge(merged_diagnostic, diag)
            )

    return ProviderFulfilment(
        state=state,
        message=message,
        target_units=requested_count,
        fulfilled_units=_sum_optional_counts([c.fulfilled_units for c in contributors]),
        running_count=_sum_optional_counts([c.running_count for c in contributors]),
        pending_count=_sum_optional_counts([c.pending_count for c in contributors]),
        failed_count=_sum_optional_counts([c.failed_count for c in contributors]),
        final=final,
        diagnostic=merged_diagnostic,
    )


def build_diagnostic_for_errors(
    fleet_errors: Optional[list[dict[str, Any]]],
) -> Any:
    """Classify fleet errors into a FulfilmentDiagnostic (or None when empty).

    Thin wrapper over :func:`classify_aws_errors` so handlers can attach a
    diagnostic to a partial/failed ``ProviderFulfilment`` without importing the
    classifier at every call site.
    """
    if not fleet_errors:
        return None
    from orb.providers.aws.infrastructure.handlers.shared.error_classifier import (
        classify_aws_errors,
    )

    return classify_aws_errors(fleet_errors, now=datetime.now(timezone.utc))


def compute_capacity_based_fulfilment(
    target_capacity: Optional[int],
    fulfilled_capacity: float,
    running_count: int,
    pending_count: int,
    failed_count: int,
    provider_label: str,
) -> ProviderFulfilment:
    """Compute ProviderFulfilment for a capacity-unit based fleet.

    Used by EC2 Fleet (Maintain/Request) and Spot Fleet handlers.

    Args:
        target_capacity: The fleet's TargetCapacity, or None if unknown.
        fulfilled_capacity: The fleet's FulfilledCapacity as reported by AWS.
        running_count: Number of instances whose status is "running".
        pending_count: Number of instances whose status is "pending" or "starting".
        failed_count: Number of instances whose status is "failed" or "error".
        provider_label: Label used in messages, e.g. "Fleet" or "Spot Fleet".
    """
    target_units = target_capacity if target_capacity is not None else int(fulfilled_capacity)
    fleet_fully_fulfilled = target_capacity is not None and fulfilled_capacity >= target_capacity

    if fleet_fully_fulfilled and pending_count == 0 and failed_count == 0:
        return ProviderFulfilment(
            state="fulfilled",
            message=(
                f"{provider_label} fulfilled: {running_count} instance(s) running "
                f"({fulfilled_capacity}/{target_capacity} capacity units)"
            ),
            target_units=target_units,
            fulfilled_units=int(fulfilled_capacity),
            running_count=running_count,
            pending_count=pending_count,
            failed_count=failed_count,
        )
    elif failed_count > 0 and running_count == 0 and pending_count == 0:
        return ProviderFulfilment(
            state="failed",
            message=f"{provider_label} failed: {failed_count} instance(s) failed",
            target_units=target_units,
            fulfilled_units=int(fulfilled_capacity),
            running_count=running_count,
            pending_count=pending_count,
            failed_count=failed_count,
        )
    else:
        return ProviderFulfilment(
            state="in_progress",
            message=(
                f"{provider_label}: {running_count} running, {pending_count} pending "
                f"({fulfilled_capacity}/{target_units} capacity units)"
            ),
            target_units=target_units,
            fulfilled_units=int(fulfilled_capacity),
            running_count=running_count,
            pending_count=pending_count,
            failed_count=failed_count,
        )


def _capacity_diagnostic(fulfilled: int, target: int) -> Any:
    """Build a CAPACITY diagnostic for a partial fleet with no explicit errors.

    A partial poll verdict with no AWS error payload still needs a *why* — the
    capacity simply did not arrive. This yields a CAPACITY-category diagnostic
    so the request DTO can explain the shortfall.
    """
    from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic

    return FulfilmentDiagnostic(
        category=DiagnosticCategory.CAPACITY,
        summary=f"Partially fulfilled: {fulfilled}/{target} capacity",
        occurred_at=datetime.now(timezone.utc),
    )


def compute_ec2fleet_fulfilment(
    fleet_type: "AWSFleetType | None",
    instances: list[dict[str, Any]],
    target_capacity: Optional[int],
    fulfilled_capacity: float,
    requested_count: int,
) -> ProviderFulfilment:
    """Compute ProviderFulfilment for an EC2 Fleet request.

    Instant fleets use count-based semantics (same as RunInstances):
    ``running_count >= requested_count`` and ``failed_count == 0`` → fulfilled.

    Maintain / Request fleets use capacity-unit semantics delegated to
    :func:`compute_capacity_based_fulfilment`.

    Args:
        fleet_type: The ``AWSFleetType`` enum value, or ``None`` if unknown.
        instances: List of instance-status dicts (each must have a ``"status"`` key).
        target_capacity: The fleet's TargetCapacity, or None if unknown.
        fulfilled_capacity: The fleet's FulfilledCapacity as reported by AWS.
        requested_count: Number of instances originally requested.
    """
    # Import here to avoid a circular dependency at module load time.
    # fleet_fulfilment is in ``shared/`` and aws_template_aggregate is a peer
    # domain object — the TYPE_CHECKING guard above keeps pyright happy for
    # type annotations while this runtime import is negligible (cached).
    from orb.providers.aws.domain.template.value_objects import AWSFleetType

    running_count = sum(1 for i in instances if i.get("status") == "running")
    pending_count = sum(1 for i in instances if i.get("status") in ("pending", "starting"))
    failed_count = sum(1 for i in instances if i.get("status") in ("failed", "error"))
    target_units = target_capacity if target_capacity is not None else requested_count

    if fleet_type == AWSFleetType.INSTANT:
        # Instant fleet: synchronous result, count-based (same as RunInstances)
        if running_count >= requested_count and failed_count == 0:
            return ProviderFulfilment(
                state="fulfilled",
                message=f"Instant fleet: {running_count} instance(s) running",
                target_units=target_units,
                fulfilled_units=running_count,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        elif pending_count > 0:
            return ProviderFulfilment(
                state="in_progress",
                message=f"Instant fleet: {running_count}/{requested_count} running, {pending_count} pending",
                target_units=target_units,
                fulfilled_units=running_count,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        # requires_async_polling=True for instant — pending state must be observed
        elif running_count > 0:
            return ProviderFulfilment(
                state="partial",
                message=f"Instant fleet: {running_count}/{requested_count} instance(s) running",
                target_units=target_units,
                fulfilled_units=running_count,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
                # Instant fleet is synchronous: this partial is settled, the
                # remaining capacity will never arrive — terminalise immediately.
                final=True,
                diagnostic=_capacity_diagnostic(running_count, requested_count),
            )
        elif not instances:
            return ProviderFulfilment(
                state="in_progress",
                message="Instant fleet: waiting for instances",
                target_units=target_units,
                fulfilled_units=0,
                running_count=0,
                pending_count=0,
                failed_count=0,
            )
        else:
            return ProviderFulfilment(
                state="failed",
                message="Instant fleet: all instances failed",
                target_units=target_units,
                fulfilled_units=0,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
    else:
        # Maintain / Request fleet: capacity-unit based fulfilment
        return compute_capacity_based_fulfilment(
            target_capacity=target_capacity,
            fulfilled_capacity=fulfilled_capacity,
            running_count=running_count,
            pending_count=pending_count,
            failed_count=failed_count,
            provider_label="Fleet",
        )

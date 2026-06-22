"""Shared capacity-based fleet fulfilment computation.

Both EC2 Fleet (Maintain/Request types) and Spot Fleet share identical
fulfilment semantics: FulfilledCapacity >= TargetCapacity AND no pending
or failed instances → fulfilled.  The only difference is the label used
in human-readable messages.
"""

from __future__ import annotations

from typing import Optional

from orb.domain.base.provider_fulfilment import ProviderFulfilment


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

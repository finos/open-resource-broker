"""Typed capacity snapshot from AWS fleet APIs.

Each AWS fleet type (EC2 Fleet, Spot Fleet) reports capacity differently but
the information needed to compute fulfilment is the same: how many units were
requested, how many are currently fulfilled, and how many instances are in each
lifecycle state.

``FleetCapacityFulfilment`` is the normalised intermediate object produced by
the per-fleet status fetchers and consumed by the shared
``compute_capacity_based_fulfilment`` helper.  Replacing anonymous dicts with
this frozen dataclass makes capacity data type-safe and self-documenting.

RunInstances has no fleet capacity concept — its check path does not use this
object.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FleetCapacityFulfilment:
    """Capacity snapshot returned by AWS describe-fleet APIs.

    Normalised across EC2 Fleet and Spot Fleet so the shared fulfilment
    computation function (``compute_capacity_based_fulfilment``) receives a
    single, typed object instead of individual keyword arguments.

    Attributes:
        target_capacity_units: The fleet's requested capacity
            (``TotalTargetCapacity`` for EC2 Fleet,
            ``TargetCapacity`` for Spot Fleet).  ``None`` when AWS does not
            return the field (rare edge-case — callers fall back to
            ``requested_count``).
        fulfilled_capacity_units: Capacity units currently allocated by AWS
            (``FulfilledCapacity``).  Zero when no capacity has been
            provisioned yet.
        provisioned_instance_count: Number of instances currently in an active
            lifecycle state (running + pending).  Derived by the fetcher from
            the describe-fleet-instances response.
        fulfillment_complete: ``True`` when AWS reports that fulfilled capacity
            meets or exceeds the target capacity
            (``fulfilled_capacity_units >= target_capacity_units``).
            ``False`` when the fleet is still filling or the target is unknown.
    """

    target_capacity_units: int | None
    fulfilled_capacity_units: int
    provisioned_instance_count: int
    fulfillment_complete: bool

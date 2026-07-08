"""Fleet release decision logic.

Centralises the branching rules for EC2 Fleet and Spot Fleet release operations
so both release managers share identical semantics.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FleetCapacityInput:
    """Input parameters for the fleet-release decision function.

    Encapsulates all capacity information needed to decide how to release a
    fleet so that the decision function has a stable, typed interface.

    Attributes:
        fleet_type: Raw fleet type string (``"request"``, ``"maintain"``, or
            ``"instant"``).  Enum values are accepted — normalised to a
            lowercase plain string internally.
        target_capacity_units: Current ``TotalTargetCapacity`` /
            ``TargetCapacity`` of the fleet.
        instances_to_return_count: Number of instance IDs in the batch being
            returned.  Used for informational purposes; the actual full-return
            determination uses *instance_weighted_capacity_units*.
        instance_weighted_capacity_units: Sum of ``WeightedCapacity`` across
            all instances being returned.  For unweighted fleets this equals
            *instances_to_return_count*.  Using the weighted sum prevents a
            race window where AWS would refill capacity units not decremented
            by the correct amount.
    """

    fleet_type: str
    target_capacity_units: int
    instances_to_return_count: int
    instance_weighted_capacity_units: int


@dataclass(frozen=True)
class FleetReleaseDecision:
    requires_capacity_reduction: bool  # True for both maintain and request types
    has_fleet_record: bool  # True for fleet types, False for RunInstances
    is_full_return: bool  # True when all instances are being returned


def compute_fleet_release_decision(
    input: FleetCapacityInput,
) -> FleetReleaseDecision:
    """Compute what actions are required when returning instances from a fleet.

    Args:
        input: A :class:`FleetCapacityInput` describing the fleet type and
               capacity numbers for this release operation.

    Returns:
        A :class:`FleetReleaseDecision` describing which actions the caller must take.
    """
    # Normalise fleet_type: handles both plain strings and str-enum values like
    # AWSFleetType.MAINTAIN whose str() representation is "AWSFleetType.MAINTAIN".
    # Using .value if available gives us the underlying "maintain" string directly.
    raw = input.fleet_type
    if hasattr(input.fleet_type, "value"):
        raw = input.fleet_type.value  # type: ignore[union-attr]
    fleet_type_lower = str(raw).lower()

    remaining = max(0, input.target_capacity_units - input.instance_weighted_capacity_units)
    is_full = remaining == 0

    if fleet_type_lower == "maintain":
        return FleetReleaseDecision(
            requires_capacity_reduction=True,
            has_fleet_record=True,
            is_full_return=is_full,
        )
    elif fleet_type_lower == "request":
        return FleetReleaseDecision(
            requires_capacity_reduction=False,
            has_fleet_record=True,
            is_full_return=is_full,
        )
    else:
        # instant or unknown — fleet record already gone, no capacity to modify
        return FleetReleaseDecision(
            requires_capacity_reduction=False,
            has_fleet_record=False,
            is_full_return=is_full,
        )

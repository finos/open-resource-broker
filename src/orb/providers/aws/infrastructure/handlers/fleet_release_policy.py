"""Fleet release decision logic.

Centralises the branching rules for EC2 Fleet and Spot Fleet release operations
so both release managers share identical semantics.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FleetReleaseDecision:
    requires_capacity_reduction: bool  # True for both maintain and request types
    has_fleet_record: bool  # True for fleet types, False for RunInstances
    is_full_return: bool  # True when all instances are being returned


def compute_fleet_release_decision(
    fleet_type: str,
    current_capacity: int,
    instances_to_return: int,
) -> FleetReleaseDecision:
    """Compute what actions are required when returning instances from a fleet.

    Args:
        fleet_type: Raw fleet type string (e.g. "maintain", "request", "instant").
                    Accepts enum values — normalised to lowercase string internally.
        current_capacity: Current TotalTargetCapacity / TargetCapacity of the fleet.
        instances_to_return: Number of instances being returned in this call.

    Returns:
        A FleetReleaseDecision describing which actions the caller must take.
    """
    # Normalise: handles both plain strings and str-enum values like
    # AWSFleetType.MAINTAIN whose str() representation is "AWSFleetType.MAINTAIN".
    # Using .value if available gives us the underlying "maintain" string directly.
    raw = fleet_type
    if hasattr(fleet_type, "value"):
        raw = fleet_type.value  # type: ignore[union-attr]
    fleet_type_lower = str(raw).lower()

    remaining = max(0, current_capacity - instances_to_return)
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

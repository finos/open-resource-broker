"""Shared instance override builder for EC2 Fleet and Spot Fleet.

Both fleet types expand machine_types x subnet_ids into a list of override
dicts. The shape differs only in optional fields (Priority, SpotPrice).
This module owns that cartesian expansion so neither config builder
duplicates it.
"""

from typing import Any, Optional


def build_fleet_overrides(
    machine_types: Optional[dict[str, Any]],
    subnet_ids: Optional[list[str]],
    abis_requirements: Optional[dict[str, Any]] = None,
    include_priority: bool = False,
    max_price: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """Build launch-template override dicts for EC2 Fleet or Spot Fleet.

    Performs a cartesian expansion of machine_types x subnet_ids.  When
    neither is present but abis_requirements is provided, emits one override
    per subnet (or a single subnet-less override) carrying InstanceRequirements.

    Args:
        machine_types: Mapping of instance-type string to weighted capacity.
            When None or empty the function falls back to abis_requirements or
            returns an empty list.
        subnet_ids: List of subnet IDs to expand across.  When None the
            overrides are emitted without a SubnetId key.
        abis_requirements: InstanceRequirements payload for ABIS-style
            attribute-based instance selection.  Injected only when
            machine_types is absent.
        include_priority: When True, a 1-based Priority field is added to
            every override (required by Spot Fleet API).
        max_price: Per-override spot price cap.  Serialised to string and
            added as SpotPrice when include_priority is True (Spot Fleet
            semantics).  Ignored for EC2 Fleet overrides.

    Returns:
        List of override dicts ready for LaunchTemplateConfigs[0]["Overrides"].
    """
    spot_price_str = str(max_price) if max_price is not None else None

    # ABIS path: no explicit machine types, use InstanceRequirements instead
    if not machine_types and abis_requirements:
        if subnet_ids:
            return [
                {"SubnetId": subnet_id, "InstanceRequirements": abis_requirements}
                for subnet_id in subnet_ids
            ]
        return [{"InstanceRequirements": abis_requirements}]

    if not machine_types:
        if subnet_ids:
            return [{"SubnetId": subnet_id} for subnet_id in subnet_ids]
        return []

    overrides: list[dict[str, Any]] = []
    types_list = list(machine_types.items())

    if subnet_ids:
        for subnet_id in subnet_ids:
            for idx, (instance_type, weight) in enumerate(types_list):
                override: dict[str, Any] = {
                    "SubnetId": subnet_id,
                    "InstanceType": instance_type,
                    "WeightedCapacity": weight,
                }
                if include_priority:
                    override["Priority"] = idx + 1
                    if spot_price_str:
                        override["SpotPrice"] = spot_price_str
                overrides.append(override)
    else:
        for idx, (instance_type, weight) in enumerate(types_list):
            override = {
                "InstanceType": instance_type,
                "WeightedCapacity": weight,
            }
            if include_priority:
                override["Priority"] = idx + 1
                if spot_price_str:
                    override["SpotPrice"] = spot_price_str
            overrides.append(override)

    return overrides

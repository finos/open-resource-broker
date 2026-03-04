from typing import Any


def build_ec2_fleet_overrides(
    machine_types: dict[str, Any] | None,
    machine_types_ondemand: dict[str, Any] | None,
    subnet_ids: list[str] | None,
    is_heterogeneous: bool,
    machine_types_priority: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    overrides: list[dict[str, Any]] = []

    # Apply priority ordering when provided: lower priority value = higher precedence
    _priority: dict[str, int] = machine_types_priority or {}

    def _sorted_types(types: dict[str, Any]) -> list[tuple[str, Any]]:
        if _priority:
            return sorted(types.items(), key=lambda kv: _priority.get(kv[0], 999))
        return list(types.items())

    if machine_types and subnet_ids:
        for subnet_id in subnet_ids:
            for instance_type, weight in _sorted_types(machine_types):
                overrides.append(
                    {
                        "SubnetId": subnet_id,
                        "InstanceType": instance_type,
                        "WeightedCapacity": weight,
                    }
                )
            if is_heterogeneous and machine_types_ondemand:
                for instance_type, weight in _sorted_types(machine_types_ondemand):
                    overrides.append(
                        {
                            "SubnetId": subnet_id,
                            "InstanceType": instance_type,
                            "WeightedCapacity": weight,
                        }
                    )
    elif machine_types:
        for instance_type, weight in _sorted_types(machine_types):
            overrides.append({"InstanceType": instance_type, "WeightedCapacity": weight})
        if is_heterogeneous and machine_types_ondemand:
            for instance_type, weight in _sorted_types(machine_types_ondemand):
                overrides.append({"InstanceType": instance_type, "WeightedCapacity": weight})
    elif subnet_ids:
        for subnet_id in subnet_ids:
            overrides.append({"SubnetId": subnet_id})

    return overrides


def build_spot_fleet_overrides(
    machine_types: dict[str, Any] | None,
    machine_types_ondemand: dict[str, Any] | None,
    subnet_ids: list[str] | None,
    max_price: Any,
    is_heterogeneous: bool,
    machine_types_priority: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    spot_price_str = str(max_price) if max_price else None
    overrides: list[dict[str, Any]] = []

    # Apply priority ordering when provided: lower priority value = higher precedence
    _priority: dict[str, int] = machine_types_priority or {}

    def _sorted_types(types: dict[str, Any]) -> list[tuple[str, Any]]:
        if _priority:
            return sorted(types.items(), key=lambda kv: _priority.get(kv[0], 999))
        return list(types.items())

    if machine_types and subnet_ids:
        if is_heterogeneous and machine_types_ondemand:
            for subnet_id in subnet_ids:
                for idx, (instance_type, weight) in enumerate(_sorted_types(machine_types)):
                    override: dict[str, Any] = {
                        "SubnetId": subnet_id,
                        "InstanceType": instance_type,
                        "WeightedCapacity": weight,
                        "Priority": idx + 1,
                    }
                    if spot_price_str:
                        override["SpotPrice"] = spot_price_str
                    overrides.append(override)
                for idx, (instance_type, weight) in enumerate(
                    _sorted_types(machine_types_ondemand)
                ):
                    overrides.append(
                        {
                            "SubnetId": subnet_id,
                            "InstanceType": instance_type,
                            "WeightedCapacity": weight,
                            "Priority": idx + len(machine_types) + 1,
                        }
                    )
        else:
            for subnet_id in subnet_ids:
                for idx, (instance_type, weight) in enumerate(_sorted_types(machine_types)):
                    override = {
                        "SubnetId": subnet_id,
                        "InstanceType": instance_type,
                        "WeightedCapacity": weight,
                        "Priority": idx + 1,
                    }
                    if spot_price_str:
                        override["SpotPrice"] = spot_price_str
                    overrides.append(override)
    elif machine_types:
        if is_heterogeneous and machine_types_ondemand:
            spot_overrides = []
            for idx, (instance_type, weight) in enumerate(_sorted_types(machine_types)):
                override = {
                    "InstanceType": instance_type,
                    "WeightedCapacity": weight,
                    "Priority": idx + 1,
                }
                if spot_price_str:
                    override["SpotPrice"] = spot_price_str
                spot_overrides.append(override)
            ondemand_overrides = [
                {
                    "InstanceType": instance_type,
                    "WeightedCapacity": weight,
                    "Priority": idx + len(machine_types) + 1,
                }
                for idx, (instance_type, weight) in enumerate(_sorted_types(machine_types_ondemand))
            ]
            overrides = spot_overrides + ondemand_overrides
        else:
            for idx, (instance_type, weight) in enumerate(_sorted_types(machine_types)):
                override = {
                    "InstanceType": instance_type,
                    "WeightedCapacity": weight,
                    "Priority": idx + 1,
                }
                if spot_price_str:
                    override["SpotPrice"] = spot_price_str
                overrides.append(override)
    elif subnet_ids:
        for subnet_id in subnet_ids:
            overrides.append({"SubnetId": subnet_id})

    return overrides

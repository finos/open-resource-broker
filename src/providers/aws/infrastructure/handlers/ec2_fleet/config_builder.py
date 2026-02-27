"""EC2 Fleet configuration builder.

Encapsulates all config-construction logic for EC2 Fleet API calls,
including native-spec processing and legacy fallback.
"""

from typing import Any, Optional

from domain.base.ports import LoggingPort
from domain.base.ports.configuration_port import ConfigurationPort
from domain.request.aggregate import Request
from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from providers.aws.domain.template.value_objects import AWSFleetType
from providers.aws.infrastructure.tags import build_system_tags, merge_tags


class EC2FleetConfigBuilder:
    """Builds the create_fleet API payload from a template and request.

    Responsibilities:
    - Native-spec processing with merge support (when native_spec_service provided)
    - Legacy config construction fallback
    - Allocation strategy mapping (spot and on-demand)
    """

    def __init__(
        self,
        native_spec_service: Optional[Any],
        config_port: Optional[ConfigurationPort],
        logger: LoggingPort,
    ) -> None:
        self._native_spec_service = native_spec_service
        self._config_port = config_port
        self._logger = logger

    def build(
        self,
        template: AWSTemplate,
        request: Request,
        lt_id: str,
        lt_version: str,
    ) -> dict[str, Any]:
        """Build the create_fleet API payload.

        Tries native-spec processing first; falls back to legacy construction
        when no native spec service is available or the spec returns nothing.

        Args:
            template: The AWS template describing the fleet.
            request: The incoming request (capacity, metadata).
            lt_id: Launch template ID to embed in the config.
            lt_version: Launch template version string.

        Returns:
            Dict suitable for passing directly to ec2_client.create_fleet(**payload).
        """
        if self._native_spec_service:
            context = self._prepare_template_context(template, request)
            context.update(
                {
                    "launch_template_id": lt_id,
                    "launch_template_version": lt_version,
                }
            )

            native_spec = self._native_spec_service.process_provider_api_spec_with_merge(
                template, request, "ec2fleet", context
            )
            if native_spec:
                if "LaunchTemplateConfigs" in native_spec:
                    native_spec["LaunchTemplateConfigs"][0]["LaunchTemplateSpecification"] = {
                        "LaunchTemplateId": lt_id,
                        "Version": lt_version,
                    }
                    if template.abis_instance_requirements:
                        overrides = native_spec["LaunchTemplateConfigs"][0].get("Overrides", [])
                        if not any("InstanceRequirements" in o for o in overrides):
                            native_spec["LaunchTemplateConfigs"][0]["Overrides"] = [
                                {"InstanceRequirements": template.get_instance_requirements_payload()}
                            ]
                self._logger.info(
                    "Using native provider API spec with merge for template %s",
                    template.template_id,
                )
                return native_spec

            return self._native_spec_service.render_default_spec("ec2fleet", context)

        return self._build_legacy(template, request, lt_id, lt_version)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_legacy(
        self,
        template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> dict[str, Any]:
        """Build EC2 Fleet config using legacy (non-native-spec) logic."""
        fleet_config: dict[str, Any] = {
            "LaunchTemplateConfigs": [
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": launch_template_id,
                        "Version": launch_template_version,
                    }
                }
            ],
            "TargetCapacitySpecification": {"TotalTargetCapacity": request.requested_count},
            "Type": template.fleet_type.value  # type: ignore[union-attr]
            if hasattr(template.fleet_type, "value")
            else str(template.fleet_type),
            "TagSpecifications": [],
        }

        assert self._config_port is not None, "config_port must be injected"
        user_tags: list[dict[str, str]] = [
            {
                "Key": "Name",
                "Value": f"{self._config_port.get_resource_prefix('fleet')}{request.request_id}",
            }
        ]
        if template.tags:
            user_tags.extend([{"Key": k, "Value": str(v)} for k, v in template.tags.items()])
        fleet_tags = merge_tags(
            user_tags,
            build_system_tags(
                request_id=str(request.request_id),
                template_id=str(template.template_id),
                provider_api="EC2Fleet",
            ),
        )
        fleet_config["TagSpecifications"] = [
            {"ResourceType": "fleet", "Tags": fleet_tags},
            {"ResourceType": "instance", "Tags": fleet_tags},
        ]

        if template.fleet_type == AWSFleetType.MAINTAIN:
            fleet_config["ReplaceUnhealthyInstances"] = True
            fleet_config["ExcessCapacityTerminationPolicy"] = "termination"

        price_type = template.price_type or "ondemand"
        if price_type == "ondemand":
            fleet_config["TargetCapacitySpecification"]["DefaultTargetCapacityType"] = "on-demand"
        elif price_type == "spot":
            fleet_config["TargetCapacitySpecification"]["DefaultTargetCapacityType"] = "spot"
            if template.allocation_strategy:
                fleet_config["SpotOptions"] = {
                    "AllocationStrategy": self._get_allocation_strategy(template.allocation_strategy)
                }
            if template.max_price is not None:
                if "SpotOptions" not in fleet_config:
                    fleet_config["SpotOptions"] = {}
                fleet_config["SpotOptions"]["MaxTotalPrice"] = str(template.max_price)
        elif price_type == "heterogeneous":
            percent_on_demand = template.percent_on_demand or 0
            on_demand_count = int(request.requested_count * percent_on_demand / 100)
            spot_count = request.requested_count - on_demand_count

            fleet_config["TargetCapacitySpecification"]["OnDemandTargetCapacity"] = on_demand_count
            fleet_config["TargetCapacitySpecification"]["SpotTargetCapacity"] = spot_count
            fleet_config["TargetCapacitySpecification"]["DefaultTargetCapacityType"] = "on-demand"

            if template.allocation_strategy:
                fleet_config["SpotOptions"] = {
                    "AllocationStrategy": self._get_allocation_strategy(template.allocation_strategy)
                }
            if template.allocation_strategy_on_demand:
                fleet_config["OnDemandOptions"] = {
                    "AllocationStrategy": self._get_allocation_strategy_on_demand(
                        template.allocation_strategy_on_demand.value
                        if hasattr(template.allocation_strategy_on_demand, "value")
                        else str(template.allocation_strategy_on_demand)
                    )
                }
            if template.max_price is not None:
                if "SpotOptions" not in fleet_config:
                    fleet_config["SpotOptions"] = {}
                fleet_config["SpotOptions"]["MaxTotalPrice"] = str(template.max_price)

        instance_requirements_payload = template.get_instance_requirements_payload()
        if instance_requirements_payload:
            overrides = []
            if template.subnet_ids:
                for subnet_id in template.subnet_ids:
                    overrides.append(
                        {
                            "SubnetId": subnet_id,
                            "InstanceRequirements": instance_requirements_payload,
                        }
                    )
            else:
                overrides.append({"InstanceRequirements": instance_requirements_payload})
            fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = overrides
        else:
            from providers.aws.infrastructure.handlers.fleet_override_builder import (
                build_ec2_fleet_overrides,
            )

            overrides = build_ec2_fleet_overrides(
                template.machine_types,
                template.machine_types_ondemand,
                template.subnet_ids,
                price_type == "heterogeneous",
                machine_types_priority=getattr(template, "machine_types_priority", None) or None,
            )
            if overrides:
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = overrides

        if template.context:
            fleet_config["Context"] = template.context

        return fleet_config

    def _prepare_template_context(self, template: AWSTemplate, request: Request) -> dict[str, Any]:
        """Build the context dict used by the native spec renderer."""
        assert self._config_port is not None, "config_port must be injected"

        instance_overrides = []
        if template.machine_types and template.subnet_ids:
            for subnet_id in template.subnet_ids:
                for instance_type, weight in template.machine_types.items():
                    instance_overrides.append(
                        {
                            "instance_type": instance_type,
                            "subnet_id": subnet_id,
                            "weighted_capacity": weight,
                        }
                    )
        elif template.machine_types:
            for instance_type, weight in template.machine_types.items():
                instance_overrides.append(
                    {"instance_type": instance_type, "weighted_capacity": weight}
                )

        ondemand_overrides = []
        if (
            template.price_type == "heterogeneous"
            and hasattr(template, "machine_types_ondemand")
            and template.machine_types_ondemand
        ):
            for instance_type, weight in template.machine_types_ondemand.items():
                ondemand_overrides.append(
                    {"instance_type": instance_type, "weighted_capacity": weight}
                )

        percent_on_demand = template.percent_on_demand or 0
        on_demand_count = int(request.requested_count * percent_on_demand / 100)
        spot_count = request.requested_count - on_demand_count

        abis_instance_requirements = template.get_instance_requirements_payload()
        has_abis = bool(abis_instance_requirements)

        if has_abis and not instance_overrides:
            if template.subnet_ids:
                for subnet_id in template.subnet_ids:
                    instance_overrides.append(
                        {
                            "instance_type": None,
                            "subnet_id": subnet_id,
                            "weighted_capacity": None,
                            "instance_requirements": abis_instance_requirements,
                        }
                    )
            else:
                instance_overrides.append(
                    {
                        "instance_type": None,
                        "subnet_id": None,
                        "weighted_capacity": None,
                        "instance_requirements": abis_instance_requirements,
                    }
                )

        price_type = template.price_type or "ondemand"
        default_capacity_type: str
        if price_type == "spot":
            default_capacity_type = "spot"
        elif price_type == "ondemand":
            default_capacity_type = "on-demand"
        else:
            default_capacity_type = "on-demand"

        return {
            "fleet_type": template.fleet_type.value,  # type: ignore[union-attr]
            "fleet_name": f"{self._config_port.get_resource_prefix('fleet')}{request.request_id}",
            "instance_overrides": instance_overrides,
            "ondemand_overrides": ondemand_overrides,
            "needs_overrides": bool(instance_overrides or ondemand_overrides),
            "is_maintain_fleet": template.fleet_type == AWSFleetType.MAINTAIN,
            "is_instant_fleet": template.fleet_type == AWSFleetType.INSTANT,
            "replace_unhealthy": template.fleet_type == AWSFleetType.MAINTAIN,
            "has_spot_options": bool(template.allocation_strategy or template.max_price),
            "has_ondemand_options": bool(template.allocation_strategy_on_demand),
            "is_heterogeneous": template.price_type == "heterogeneous",
            "abis_instance_requirements": abis_instance_requirements,
            "has_abis": has_abis,
            "percent_on_demand": percent_on_demand,
            "on_demand_count": on_demand_count,
            "spot_count": spot_count,
            "allocation_strategy": (
                self._get_allocation_strategy(template.allocation_strategy)
                if template.allocation_strategy
                else None
            ),
            "allocation_strategy_on_demand": (
                self._get_allocation_strategy_on_demand(
                    template.allocation_strategy_on_demand.value
                    if hasattr(template.allocation_strategy_on_demand, "value")
                    else str(template.allocation_strategy_on_demand)
                )
                if template.allocation_strategy_on_demand
                else None
            ),
            "max_spot_price": (str(template.max_price) if template.max_price is not None else None),
            "default_capacity_type": default_capacity_type,
        }

    def _get_allocation_strategy(self, strategy: str) -> str:
        """Map a Symphony spot allocation strategy name to the EC2 Fleet API value."""
        strategy_map = {
            "capacityOptimized": "capacity-optimized",
            "capacityOptimizedPrioritized": "capacity-optimized-prioritized",
            "diversified": "diversified",
            "lowestPrice": "lowest-price",
            "priceCapacityOptimized": "price-capacity-optimized",
        }
        return strategy_map.get(strategy, "lowest-price")

    def _get_allocation_strategy_on_demand(self, strategy: str) -> str:
        """Map a Symphony on-demand allocation strategy name to the EC2 Fleet API value."""
        strategy_map = {"lowestPrice": "lowest-price", "prioritized": "prioritized"}
        return strategy_map.get(strategy, "lowest-price")

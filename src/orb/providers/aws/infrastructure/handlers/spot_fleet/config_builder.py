"""SpotFleet configuration builder.

Encapsulates all config-construction logic for AWS Spot Fleet requests,
keeping SpotFleetHandler focused on orchestration.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.aggregate import Request
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.domain.template.value_objects import AWSFleetType
from orb.providers.aws.infrastructure.handlers.shared.base_config_builder import BaseConfigBuilder
from orb.providers.aws.infrastructure.tags import build_resource_tags


class SpotFleetConfigBuilder(BaseConfigBuilder):
    """Builds the SpotFleetRequestConfig dict passed to request_spot_fleet."""

    def __init__(
        self,
        native_spec_service: Any,
        config_port: Optional[ConfigurationPort],
        logger: LoggingPort,
    ) -> None:
        super().__init__(native_spec_service, config_port, logger)

    def _api_key(self) -> str:
        return "spotfleet"

    def _inject_launch_template(
        self,
        native_spec: dict[str, Any],
        template: AWSTemplate,
        lt_id: str,
        lt_version: str,
    ) -> None:
        """Patch LT id/version into the SpotFleet native spec in-place."""
        if "LaunchSpecifications" in native_spec:
            for spec in native_spec["LaunchSpecifications"]:
                if "LaunchTemplate" not in spec:
                    spec["LaunchTemplate"] = {}
                spec["LaunchTemplate"]["LaunchTemplateId"] = lt_id
                spec["LaunchTemplate"]["Version"] = lt_version
        if template.abis_instance_requirements:
            if "LaunchTemplateConfigs" in native_spec:
                overrides = native_spec["LaunchTemplateConfigs"][0].get("Overrides", [])
                if not any("InstanceRequirements" in o for o in overrides):
                    native_spec["LaunchTemplateConfigs"][0]["Overrides"] = [
                        {"InstanceRequirements": template.get_instance_requirements_payload()}
                    ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        template: AWSTemplate,
        request: Request,
        lt_id: str,
        lt_version: str,
    ) -> dict[str, Any]:
        """Return the SpotFleetRequestConfig dict for request_spot_fleet.

        Tries native spec processing first; falls back to legacy construction
        when no native spec service is available.
        """
        if self._native_spec_service:
            native_spec = self._process_native_spec(template, request, lt_id, lt_version)
            if native_spec:
                return native_spec

            default_spec = self._render_default(template, request, lt_id, lt_version)
            if template.abis_instance_requirements:
                if "LaunchTemplateConfigs" in default_spec:
                    overrides = default_spec["LaunchTemplateConfigs"][0].get("Overrides", [])
                    if not any("InstanceRequirements" in o for o in overrides):
                        default_spec["LaunchTemplateConfigs"][0]["Overrides"] = [
                            {"InstanceRequirements": template.get_instance_requirements_payload()}
                        ]
            return default_spec

        return self._build_legacy(template, request, lt_id, lt_version)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _calculate_capacity_distribution(
        self, template: AWSTemplate, requested_count: int
    ) -> dict[str, Any]:
        """Delegate capacity distribution to the template's own logic."""
        # Mirrors BaseContextMixin._calculate_capacity_distribution
        price_type = getattr(template, "price_type", "spot") or "spot"
        percent_on_demand = getattr(template, "percent_on_demand", None)

        if price_type == "ondemand":
            on_demand_count = requested_count
            spot_count = 0
        elif percent_on_demand is not None and percent_on_demand > 0:
            on_demand_count = max(1, int(requested_count * percent_on_demand / 100))
            spot_count = requested_count - on_demand_count
        else:
            on_demand_count = 0
            spot_count = requested_count

        return {
            "target_capacity": requested_count,
            "on_demand_count": on_demand_count,
            "spot_count": spot_count,
        }

    def _prepare_template_context(self, template: AWSTemplate, request: Request) -> dict[str, Any]:
        """Build the context dict used by native spec rendering."""
        requested_count = int(getattr(request, "requested_count", 1) or 1)
        capacity = self._calculate_capacity_distribution(template, requested_count)

        assert self._config_port is not None, "config_port must be injected"
        fleet_name = f"{self._config_port.get_resource_prefix('spot_fleet')}{request.request_id}"

        instance_overrides: list[dict[str, Any]] = []
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
        else:
            single_type = (
                next(iter(template.machine_types.keys())) if template.machine_types else "t3.medium"
            )
            instance_overrides.append(
                {
                    "instance_type": single_type,
                    "weighted_capacity": 1,
                    "subnet_id": template.subnet_ids[0] if template.subnet_ids else None,
                }
            )

        abis_instance_requirements = template.get_instance_requirements_payload()

        _ft = template.fleet_type
        fleet_type = (
            _ft.value if _ft is not None and hasattr(_ft, "value") else str(_ft or "request")
        )

        tag_context = self._build_tag_context(
            request_id=str(request.request_id),
            template_id=str(template.template_id),
            provider_api="SpotFleet",
            template_tags=template.tags,
        )

        context: dict[str, Any] = {
            "fleet_name": fleet_name,
            "target_capacity": capacity["target_capacity"],
            "on_demand_count": capacity["on_demand_count"],
            "spot_count": capacity["spot_count"],
            "base_launch_spec": {
                "image_id": template.image_id,
                "security_groups": template.security_group_ids or [],
            },
            "instance_overrides": instance_overrides,
            "has_overrides": len(instance_overrides) > 1,
            "fleet_role": template.fleet_role,
            "fleet_type": fleet_type,
            "is_heterogeneous": template.price_type == "heterogeneous",
            "allocation_strategy": template.get_spot_fleet_allocation_strategy(),
            "instance_interruption_behavior": getattr(
                template, "instance_interruption_behavior", "terminate"
            ),
            "replace_unhealthy_instances": getattr(template, "replace_unhealthy_instances", True),
            "spot_price": (
                str(template.max_price)
                if hasattr(template, "max_price") and template.max_price is not None
                else "0.10"
            ),
            "has_spot_price": hasattr(template, "max_price") and template.max_price is not None,
            "abis_instance_requirements": abis_instance_requirements,
            "has_abis": bool(abis_instance_requirements),
            **tag_context,
        }
        return context

    def _build_legacy(
        self,
        template: AWSTemplate,
        request: Request,
        lt_id: str,
        lt_version: str,
    ) -> dict[str, Any]:
        """Build SpotFleetRequestConfig without a native spec service."""
        fleet_role = template.fleet_role

        # Normalise service-linked role ARNs
        if fleet_role and "ec2fleet.amazonaws.com/AWSServiceRoleForEC2Fleet" in fleet_role:
            from orb.providers.aws.infrastructure.aws_client import (
                AWSClient,  # noqa: F401 – type hint only
            )

            # AWSClient is not available here; caller must pass a resolved role.
            # Log a warning and leave the role as-is — the handler should resolve it before calling.
            self._logger.warning(
                "EC2Fleet role passed to SpotFleetConfigBuilder; role may need conversion: %s",
                fleet_role,
            )
        elif fleet_role == "AWSServiceRoleForEC2SpotFleet":
            self._logger.warning(
                "Short role name passed to SpotFleetConfigBuilder; expected full ARN: %s",
                fleet_role,
            )

        requested_count = int(getattr(request, "requested_count", 0) or 1)
        capacity = self._calculate_capacity_distribution(template, requested_count)
        target_capacity = capacity["target_capacity"]
        on_demand_capacity = capacity["on_demand_count"]

        assert self._config_port is not None, "config_port must be injected"
        common_tags = build_resource_tags(
            config_port=self._config_port,
            request_id=str(request.request_id),
            template_id=str(template.template_id),
            resource_prefix_key="spot_fleet",
            provider_api="SpotFleet",
            template_tags=template.tags,
        )

        fleet_type_value = (
            template.fleet_type.value  # type: ignore[union-attr]
            if hasattr(template.fleet_type, "value")
            else template.fleet_type
        )

        fleet_config: dict[str, Any] = {
            "LaunchTemplateConfigs": [
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": lt_id,
                        "Version": lt_version,
                    }
                }
            ],
            "TargetCapacity": target_capacity,
            "IamFleetRole": fleet_role,
            "AllocationStrategy": template.get_spot_fleet_allocation_strategy(),
            "Type": fleet_type_value,
            "TagSpecifications": [
                {"ResourceType": "spot-fleet-request", "Tags": common_tags},
            ],
        }

        price_type = template.price_type or "spot"
        if price_type in ("ondemand", "heterogeneous") or on_demand_capacity > 0:
            fleet_config["OnDemandTargetCapacity"] = on_demand_capacity

        if template.fleet_type == AWSFleetType.MAINTAIN.value:
            fleet_config["ReplaceUnhealthyInstances"] = True
            fleet_config["TerminateInstancesWithExpiration"] = True

        if template.max_price:
            fleet_config["SpotPrice"] = str(template.max_price)

        instance_requirements_payload = template.get_instance_requirements_payload()
        if instance_requirements_payload:
            overrides: list[dict[str, Any]] = []
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
            from orb.providers.aws.infrastructure.handlers.shared.instance_override_builder import (
                build_fleet_overrides,
            )

            overrides = build_fleet_overrides(
                template.machine_types,
                template.subnet_ids,
                include_priority=True,
                max_price=template.max_price,
            )
            if overrides:
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = overrides

        expiry_minutes = getattr(template, "spot_fleet_request_expiry", None)
        if expiry_minutes is not None and isinstance(expiry_minutes, (int, float)):
            valid_until = datetime.now(timezone.utc) + timedelta(minutes=int(expiry_minutes))
            fleet_config["ValidUntil"] = valid_until.strftime("%Y-%m-%dT%H:%M:%SZ")

        if template.context:
            fleet_config["Context"] = template.context

        self._logger.debug(
            "Spot Fleet configuration: %s", json.dumps(fleet_config, indent=2, default=str)
        )
        return fleet_config

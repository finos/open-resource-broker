"""ASG configuration builder.

Encapsulates all Auto Scaling Group config-construction logic, including
native-spec merging, ABIS InstanceRequirements injection, and the legacy
fallback path.
"""

from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.aggregate import Request
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.exceptions.aws_exceptions import AWSConfigurationError
from orb.providers.aws.infrastructure.handlers.shared.base_config_builder import BaseConfigBuilder


class ASGConfigBuilder(BaseConfigBuilder):
    """Builds the CreateAutoScalingGroup API parameter dict for a given request."""

    def __init__(
        self,
        native_spec_service: Any,
        config_port: Optional[ConfigurationPort],
        logger: LoggingPort,
    ) -> None:
        super().__init__(native_spec_service, config_port, logger)

    def _api_key(self) -> str:
        return "asg"

    def _inject_launch_template(
        self,
        native_spec: dict[str, Any],
        template: AWSTemplate,
        lt_id: str,
        lt_version: str,
    ) -> None:
        """Patch LT id/version into the ASG native spec in-place."""
        if "MixedInstancesPolicy" in native_spec:
            mip = native_spec["MixedInstancesPolicy"]
            lt_spec = mip.setdefault("LaunchTemplate", {}).setdefault(
                "LaunchTemplateSpecification", {}
            )
            lt_spec.setdefault("LaunchTemplateId", lt_id)
            lt_spec.setdefault("Version", lt_version)
            native_spec.pop("LaunchTemplate", None)
        else:
            if "LaunchTemplate" not in native_spec:
                native_spec["LaunchTemplate"] = {}
            native_spec["LaunchTemplate"]["LaunchTemplateId"] = lt_id
            native_spec["LaunchTemplate"]["Version"] = lt_version

    def build(
        self,
        asg_name: str,
        template: AWSTemplate,
        request: Request,
        lt_id: str,
        lt_version: str,
    ) -> dict[str, Any]:
        """Return the full CreateAutoScalingGroup parameter dict.

        Tries native-spec processing first; falls back to legacy construction
        when no native spec service is configured or the spec renders nothing.
        """
        if self._native_spec_service:
            extra = {
                "asg_name": asg_name,
                "new_instances_protected_from_scale_in": True,
            }
            native_spec = self._process_native_spec(template, request, lt_id, lt_version, extra)
            if native_spec:
                native_spec["AutoScalingGroupName"] = asg_name
                native_spec.setdefault("NewInstancesProtectedFromScaleIn", True)
                self._ensure_abis_in_native_spec(native_spec, template, lt_id, lt_version)
                return native_spec

            return self._render_default(template, request, lt_id, lt_version, extra)

        return self._build_legacy(asg_name, template, request, lt_id, lt_version)

    def _ensure_abis_in_native_spec(
        self,
        native_spec: dict[str, Any],
        template: AWSTemplate,
        lt_id: str,
        lt_version: str,
    ) -> None:
        """Inject ABIS InstanceRequirements into native spec MixedInstancesPolicy if needed."""
        instance_requirements = template.get_instance_requirements_payload()
        if not instance_requirements:
            return

        if "MixedInstancesPolicy" not in native_spec:
            native_spec["MixedInstancesPolicy"] = {}

        mip = native_spec["MixedInstancesPolicy"]

        if "LaunchTemplate" not in mip:
            mip["LaunchTemplate"] = {}

        lt = mip["LaunchTemplate"]

        overrides = lt.get("Overrides", [])
        if any("InstanceRequirements" in o for o in overrides):
            return  # already present — respect it

        lt["Overrides"] = [{"InstanceRequirements": instance_requirements}]

        lt_spec = lt.setdefault("LaunchTemplateSpecification", {})
        lt_spec.setdefault("LaunchTemplateId", lt_id)
        lt_spec.setdefault("Version", lt_version)

        native_spec.pop("LaunchTemplate", None)

        self._logger.info(
            "Injected ABIS InstanceRequirements into native spec for template %s",
            template.template_id,
        )

    def _build_legacy(
        self,
        asg_name: str,
        template: AWSTemplate,
        request: Request,
        lt_id: str,
        lt_version: str,
    ) -> dict[str, Any]:
        """Build ASG config using the legacy (non-native-spec) path."""
        asg_config: dict[str, Any] = {
            "AutoScalingGroupName": asg_name,
            "MinSize": 0,
            "MaxSize": request.requested_count * 2,
            "DesiredCapacity": request.requested_count,
            "DefaultCooldown": 300,
            "HealthCheckType": "EC2",
            "HealthCheckGracePeriod": 300,
            "NewInstancesProtectedFromScaleIn": True,
        }

        instance_types_map = template.machine_types or {}
        instance_requirements_payload = template.get_instance_requirements_payload()

        if instance_requirements_payload:
            asg_config["MixedInstancesPolicy"] = {
                "LaunchTemplate": {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": lt_id,
                        "Version": lt_version,
                    },
                    "Overrides": [{"InstanceRequirements": instance_requirements_payload}],
                }
            }
        elif instance_types_map:
            overrides = []
            for itype, weight in instance_types_map.items():
                override: dict[str, Any] = {"InstanceType": itype}
                if weight:
                    override["WeightedCapacity"] = str(weight)
                overrides.append(override)

            asg_config["MixedInstancesPolicy"] = {
                "LaunchTemplate": {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": lt_id,
                        "Version": lt_version,
                    },
                    "Overrides": overrides,
                }
            }
        else:
            asg_config["LaunchTemplate"] = {
                "LaunchTemplateId": lt_id,
                "Version": lt_version,
            }

        price_type = getattr(template, "price_type", "ondemand") or "ondemand"
        percent_on_demand = getattr(template, "percent_on_demand", None)
        needs_spot_distribution = percent_on_demand is not None or price_type in (
            "spot",
            "heterogeneous",
        )

        if needs_spot_distribution:
            if "MixedInstancesPolicy" not in asg_config:
                asg_config["MixedInstancesPolicy"] = {
                    "LaunchTemplate": {
                        "LaunchTemplateSpecification": {
                            "LaunchTemplateId": lt_id,
                            "Version": lt_version,
                        }
                    }
                }

            ondemand_pct = int(percent_on_demand) if percent_on_demand is not None else 0
            instances_distribution: dict[str, Any] = {
                "OnDemandBaseCapacity": 0,
                "OnDemandPercentageAboveBaseCapacity": ondemand_pct,
            }
            asg_config["MixedInstancesPolicy"]["InstancesDistribution"] = instances_distribution

            if getattr(template, "allocation_strategy", None):
                instances_distribution["SpotAllocationStrategy"] = (
                    template.get_asg_allocation_strategy()
                )

            if "LaunchTemplate" in asg_config:
                asg_config.pop("LaunchTemplate", None)

        if template.subnet_ids:
            asg_config["VPCZoneIdentifier"] = ",".join(template.subnet_ids)

        if template.context:
            asg_config["Context"] = template.context

        return asg_config

    # --- context helpers (duplicated from handler to keep builder self-contained) ---

    def _prepare_template_context(self, template: AWSTemplate, request: Request) -> dict[str, Any]:
        """Build the template rendering context for native-spec processing."""
        if self._config_port is None:
            raise AWSConfigurationError(
                "config_port must be injected before calling _prepare_template_context"
            )

        capacity = self._calculate_capacity_distribution(template, request.requested_count)
        on_demand_count = capacity["on_demand_count"]
        spot_count = capacity["spot_count"]
        percent_on_demand = template.percent_on_demand or 0

        abis_instance_requirements = template.get_instance_requirements_payload()
        has_abis = bool(abis_instance_requirements)

        machine_types_map = template.machine_types or {}
        has_machine_types = bool(machine_types_map) and not has_abis
        machine_types_overrides = (
            [
                {
                    "instance_type": itype,
                    "weighted_capacity": str(weight) if weight else None,
                }
                for itype, weight in machine_types_map.items()
            ]
            if has_machine_types
            else []
        )

        tag_context = self._build_tag_context(
            request_id=str(request.request_id),
            template_id=str(template.template_id),
            provider_api="ASG",
            template_tags=template.tags,
        )

        return {
            "request_id": str(request.request_id),
            "requested_count": request.requested_count,
            "desired_capacity": request.requested_count,
            "image_id": template.image_id,
            "instance_type": next(iter(template.machine_types or {}), None),
            "subnet_ids": template.subnet_ids or [],
            "has_subnets": bool(template.subnet_ids),
            "security_group_ids": template.security_group_ids or [],
            "tags": template.tags or {},
            "price_type": getattr(template, "price_type", "ondemand"),
            "asg_name": f"{self._config_port.get_resource_prefix('asg')}{request.request_id}",
            "min_size": 0,
            "max_size": request.requested_count * 2,
            "default_cooldown": 300,
            "health_check_type": "EC2",
            "health_check_grace_period": 300,
            "vpc_zone_identifier": (",".join(template.subnet_ids) if template.subnet_ids else None),
            "new_instances_protected_from_scale_in": True,
            "context": (
                template.context if hasattr(template, "context") and template.context else None
            ),
            "has_context": hasattr(template, "context") and bool(template.context),
            "has_instance_protection": bool(getattr(template, "instance_protection", None)),
            "has_lifecycle_hooks": bool(getattr(template, "lifecycle_hooks", None)),
            "abis_instance_requirements": abis_instance_requirements,
            "has_abis": has_abis,
            "has_machine_types": has_machine_types,
            "machine_types_overrides": machine_types_overrides,
            "percent_on_demand": percent_on_demand,
            "on_demand_count": on_demand_count,
            "spot_count": spot_count,
            **tag_context,
        }

    def _calculate_capacity_distribution(
        self, template: AWSTemplate, requested_count: int
    ) -> dict[str, Any]:
        """Calculate on-demand / spot capacity split."""
        percent_on_demand = getattr(template, "percent_on_demand", None)
        price_type = getattr(template, "price_type", "ondemand") or "ondemand"

        if percent_on_demand is not None:
            on_demand_count = int(requested_count * percent_on_demand / 100)
            spot_count = requested_count - on_demand_count
        elif price_type == "spot":
            on_demand_count = 0
            spot_count = requested_count
        else:
            on_demand_count = requested_count
            spot_count = 0

        return {
            "on_demand_count": on_demand_count,
            "spot_count": spot_count,
        }

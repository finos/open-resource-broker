"""Base context mixin for AWS handlers."""

from datetime import datetime
from typing import Any, Dict

from domain.request.aggregate import Request
from providers.aws.domain.template.aggregate import AWSTemplate


class BaseContextMixin:
    """Shared context preparation methods for all AWS handlers."""

    def _prepare_base_context(self, template: AWSTemplate, request: Request) -> Dict[str, Any]:
        """Base context used by all handlers."""
        return {
            # Standard identifiers
            "request_id": str(request.request_id),
            "template_id": str(template.template_id),
            # Standard capacity values
            "requested_count": request.requested_count,
            "min_count": 1,
            "max_count": request.requested_count,
            # Standard timestamps
            "timestamp": datetime.utcnow().isoformat(),
            # Standard package info
            "created_by": self._get_package_name(),
        }

    def _get_package_name(self) -> str:
        """Integrated package name retrieval."""
        if hasattr(self, "config_port") and self.config_port:
            try:
                package_info = self.config_port.get_package_info()
                return package_info.get("name", "open-hostfactory-plugin")
            except Exception:  # nosec B110
                pass
        return "open-hostfactory-plugin"

    def _calculate_capacity_distribution(
        self, template: AWSTemplate, request: Request
    ) -> Dict[str, Any]:
        """Standard capacity calculation for all fleet types."""
        total_capacity = request.requested_count

        if template.price_type == "heterogeneous":
            percent_on_demand = template.percent_on_demand or 0
            on_demand_count = int(total_capacity * percent_on_demand / 100)
            spot_count = total_capacity - on_demand_count
        else:
            on_demand_count = 0 if template.price_type == "spot" else total_capacity
            spot_count = total_capacity if template.price_type == "spot" else 0

        return {
            "total_capacity": total_capacity,
            "target_capacity": total_capacity,  # For Fleet APIs
            "desired_capacity": total_capacity,  # For ASG
            "on_demand_count": on_demand_count,
            "spot_count": spot_count,
            "is_heterogeneous": template.price_type == "heterogeneous",
            "is_spot_only": template.price_type == "spot",
            "is_ondemand_only": template.price_type == "ondemand",
        }

    def _prepare_standard_tags(self, template: AWSTemplate, request: Request) -> Dict[str, Any]:
        """Standard tag preparation for all handlers."""
        created_by = self._get_package_name()

        base_tags = [
            {"key": "RequestId", "value": str(request.request_id)},
            {"key": "TemplateId", "value": str(template.template_id)},
            {"key": "CreatedBy", "value": created_by},
            {"key": "CreatedAt", "value": datetime.utcnow().isoformat()},
        ]

        custom_tags = []
        if template.tags:
            custom_tags = [{"key": k, "value": v} for k, v in template.tags.items()]

        return {
            "base_tags": base_tags,
            "custom_tags": custom_tags,
            "all_tags": base_tags + custom_tags,
            "has_custom_tags": bool(custom_tags),
        }

    def _prepare_standard_flags(self, template: AWSTemplate) -> Dict[str, Any]:
        """Standard conditional flags for all handlers."""
        return {
            # Network flags
            "has_subnets": bool(template.subnet_ids),
            "has_security_groups": bool(template.security_group_ids),
            # Configuration flags
            "has_instance_types": bool(getattr(template, "instance_types", None)),
            "has_allocation_strategy": bool(getattr(template, "allocation_strategy", None)),
            # Optional configuration flags
            "has_key_name": hasattr(template, "key_name") and bool(template.key_name),
            "has_user_data": hasattr(template, "user_data") and bool(template.user_data),
            "has_instance_profile": hasattr(template, "instance_profile")
            and bool(template.instance_profile),
            "has_ebs_optimized": hasattr(template, "ebs_optimized")
            and template.ebs_optimized is not None,
            "has_monitoring": hasattr(template, "monitoring_enabled")
            and template.monitoring_enabled is not None,
        }

    def _apply_post_creation_tagging(self, resource_id: str, request: Request, template: AWSTemplate):
        """Apply base tags after resource creation using AWSOperations."""
        if hasattr(self, 'aws_operations') and self.aws_operations:
            self.aws_operations.apply_base_tags_to_resource(resource_id, request, template)

    def _tag_fleet_instances_if_needed(self, fleet_id: str, request: Request, template: AWSTemplate):
        """Tag fleet instances based on provider_api using AWSOperations."""
        if hasattr(self, 'aws_operations') and self.aws_operations:
            if hasattr(template, 'provider_api') and template.provider_api:
                provider_api_str = template.provider_api.value if hasattr(template.provider_api, 'value') else str(template.provider_api)
                self.aws_operations.discover_and_tag_fleet_instances(fleet_id, request, template, provider_api_str)

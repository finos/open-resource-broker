"""Base context mixin for AWS handlers."""

from datetime import datetime, timezone
from typing import Any

from orb.infrastructure.logging.logger import get_logger
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate

_logger = get_logger(__name__)


class BaseContextMixin:
    """Shared context preparation methods for all AWS handlers."""

    def _prepare_base_context(
        self, template: AWSTemplate, request_id: str, requested_count: int
    ) -> dict[str, Any]:
        """Base context used by all handlers."""
        return {
            # Standard identifiers
            "request_id": str(request_id),
            "template_id": str(template.template_id),
            # Standard capacity values
            "requested_count": requested_count,
            "min_count": 1,
            "max_count": requested_count,
            # Standard timestamps
            "timestamp": datetime.now(timezone.utc).isoformat(),
            # Standard package info
            "created_by": self._get_package_name(),
        }

    def _get_package_name(self) -> str:
        """Integrated package name retrieval."""
        if hasattr(self, "config_port") and self.config_port:  # type: ignore[attr-defined]
            try:
                package_info = self.config_port.get_package_info()  # type: ignore[attr-defined]
                return package_info.get("name", "open-resource-broker")
            except Exception as e:
                _logger.debug("Could not get package name from config port: %s", e)
        return "open-resource-broker"

    def _prepare_standard_tags(self, template: AWSTemplate, request_id: str) -> dict[str, Any]:
        """Standard tag preparation for all handlers."""
        created_by = self._get_package_name()

        base_tags = [
            {"key": "RequestId", "value": str(request_id)},
            {"key": "TemplateId", "value": str(template.template_id)},
            {"key": "CreatedBy", "value": created_by},
            {"key": "CreatedAt", "value": datetime.now(timezone.utc).isoformat()},
        ]

        custom_tags = []
        if template.tags:
            custom_tags = [{"key": str(k), "value": str(v)} for k, v in template.tags.items()]

        return {
            "base_tags": base_tags,
            "custom_tags": custom_tags,
            "all_tags": base_tags + custom_tags,
            "has_custom_tags": bool(custom_tags),
        }

    def _prepare_standard_flags(self, template: AWSTemplate) -> dict[str, Any]:
        """Standard conditional flags for all handlers."""
        return {
            # Network flags
            "has_subnets": bool(template.subnet_ids),
            "has_security_groups": bool(template.security_group_ids),
            # Configuration flags
            "has_instance_types": bool(template.machine_types),
            "has_allocation_strategy": bool(template.allocation_strategy),
            # Optional configuration flags
            "has_key_name": hasattr(template, "machine_ssh_key") and bool(template.machine_ssh_key),
            "has_user_data": hasattr(template, "machine_bootstrap")
            and bool(template.machine_bootstrap),
            "has_instance_profile": hasattr(template, "machine_role")
            and bool(template.machine_role),
            "has_ebs_optimized": hasattr(template, "ebs_optimized")
            and template.ebs_optimized is not None,  # type: ignore[attr-defined]
            "has_monitoring": hasattr(template, "monitoring_enabled")
            and template.monitoring_enabled is not None,
        }

"""AWS Capability Service - Handles provider capabilities reporting."""

import re
from typing import TYPE_CHECKING, Any

from orb.domain.base.ports import LoggingPort
from orb.providers.base.strategy import ProviderCapabilities, ProviderOperationType

if TYPE_CHECKING:
    from orb.providers.aws.services.handler_registry import AWSHandlerRegistry


class AWSCapabilityService:
    """Service for AWS provider capabilities reporting and utility methods."""

    def __init__(self, handler_registry: "AWSHandlerRegistry", logger: LoggingPort):
        self._handler_registry = handler_registry
        self._logger = logger

    def get_capabilities(self) -> ProviderCapabilities:
        """Get comprehensive AWS provider capabilities."""
        try:
            if self._handler_registry:
                supported_apis = self._handler_registry.get_supported_apis()
                self._logger.debug("Supported APIs from handler registry: %s", supported_apis)
            else:
                self._logger.warning(
                    "Handler registry not available, returning empty supported APIs"
                )
                supported_apis = []
        except Exception as e:
            self._logger.error("Error getting supported APIs: %s", e)
            supported_apis = []

        return ProviderCapabilities(
            provider_type="aws",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
                ProviderOperationType.VALIDATE_TEMPLATE,
                ProviderOperationType.GET_AVAILABLE_TEMPLATES,
                ProviderOperationType.HEALTH_CHECK,
                ProviderOperationType.RESOLVE_IMAGE,
            ],
            supported_apis=supported_apis,
            features={
                "instance_management": True,
                "spot_instances": True,
                "fleet_management": True,
                "auto_scaling": True,
                "load_balancing": True,
                "vpc_support": True,
                "security_groups": True,
                "key_pairs": True,
                "tags_support": True,
                "monitoring": True,
                "regions": ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"],
                "instance_types": ["t3.micro", "t3.small", "t3.medium", "m5.large", "c5.large"],
                "max_instances_per_request": 1000,
                "supports_windows": True,
                "supports_linux": True,
            },
            limitations={
                "max_concurrent_requests": 100,
                "rate_limit_per_second": 10,
                "max_instance_lifetime_hours": 8760,
                "requires_vpc": False,
                "requires_key_pair": False,
            },
            performance_metrics={
                "typical_create_time_seconds": 60,
                "typical_terminate_time_seconds": 30,
                "health_check_timeout_seconds": 10,
            },
        )

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        """Generate AWS provider name: aws_{profile}_{region}"""
        profile = config.get("profile") or "instance-profile"
        region = config.get("region", "us-east-1")

        sanitized_profile = re.sub(r"[^a-zA-Z0-9\-_]", "-", profile)
        return f"aws_{sanitized_profile}_{region}"

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        """Parse AWS provider name back to components."""
        parts = provider_name.split("_")
        if len(parts) >= 3 and parts[0] == "aws":
            return {
                "type": "aws",
                "profile": parts[1],
                "region": "_".join(parts[2:]),  # Handle regions with underscores
            }
        return {"type": "aws", "profile": "instance-profile", "region": "us-east-1"}

    def get_provider_name_pattern(self) -> str:
        """Get the naming pattern for AWS providers."""
        return "aws_{profile}_{region}"

    def get_supported_apis(self) -> list[str]:
        """Get supported APIs from handler registry."""
        return list(self._handler_registry.get_available_handlers().keys())

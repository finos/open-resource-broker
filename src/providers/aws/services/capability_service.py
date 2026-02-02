"""AWS Capability Service - Handles provider capabilities reporting."""

from typing import TYPE_CHECKING

from domain.base.ports import LoggingPort
from providers.base.strategy import ProviderCapabilities, ProviderOperationType

if TYPE_CHECKING:
    from providers.aws.services.handler_registry import AWSHandlerRegistry


class AWSCapabilityService:
    """Service for AWS provider capabilities reporting."""

    def __init__(self, handler_registry: "AWSHandlerRegistry", logger: LoggingPort):
        self._handler_registry = handler_registry
        self._logger = logger

    def get_capabilities(self) -> ProviderCapabilities:
        """Get comprehensive AWS provider capabilities."""
        try:
            supported_apis = self._handler_registry.get_supported_apis()
            self._logger.debug("Supported APIs from handler registry: %s", supported_apis)
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
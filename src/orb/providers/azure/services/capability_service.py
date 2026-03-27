"""Azure capability reporting."""

from typing import Any

from orb.providers.azure.capabilities import get_supported_api_capabilities, get_supported_apis
from orb.providers.base.strategy import ProviderCapabilities, ProviderOperationType


class AzureCapabilityService:
    """Service for Azure provider capabilities reporting."""

    @staticmethod
    def get_capabilities() -> ProviderCapabilities:
        """Get comprehensive Azure provider capabilities."""
        # TODO: Keep Azure and AWS capability metadata dynamic together.
        # These example regions / instance types and operational heuristics are
        # still hard-coded in both providers, we should evaluate if they can be made
        # dynamic
        return ProviderCapabilities(
            provider_type="azure",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
                ProviderOperationType.VALIDATE_TEMPLATE,
                ProviderOperationType.GET_AVAILABLE_TEMPLATES,
                ProviderOperationType.HEALTH_CHECK,
            ],
            features={
                "supported_apis": get_supported_apis(),
                "api_capabilities": get_supported_api_capabilities(),
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
                "regions": ["eastus", "eastus2", "westus2", "westeurope", "northeurope"],
                "instance_types": [
                    "Standard_D2s_v5",
                    "Standard_D4s_v5",
                    "Standard_D8s_v5",
                    "Standard_E4s_v5",
                    "Standard_F4s_v2",
                ],
                "max_instances_per_request": 1000,
                "supports_windows": False,
                "supports_linux": True,
            },
            limitations={
                "max_concurrent_requests": 100,
                "rate_limit_per_second": 20,
                "max_instance_lifetime_hours": 8760,
                "requires_vpc": True,
                "requires_key_pair": True,
            },
            performance_metrics={
                "typical_create_time_seconds": 120,
                "typical_terminate_time_seconds": 60,
                "health_check_timeout_seconds": 15,
            },
        )

    @staticmethod
    def generate_provider_name(config: dict[str, Any]) -> str:
        """Generate Azure provider name: {type}_{subscription_id}_{region}."""
        subscription_id = config.get("subscription_id", "default")
        region = config.get("region", "eastus")
        return f"azure_{subscription_id}_{region}"

    @staticmethod
    def parse_provider_name(provider_name: str) -> dict[str, str]:
        """Parse Azure provider name back to components."""
        parts = provider_name.split("_")
        return {
            "type": parts[0] if len(parts) > 0 else "azure",
            "subscription_id": parts[1] if len(parts) > 1 else "default",
            "region": parts[2] if len(parts) > 2 else "eastus2",
        }

    @staticmethod
    def get_provider_name_pattern() -> str:
        """Get the naming pattern for Azure providers."""
        return "{type}_{subscription_id}_{region}"

    @staticmethod
    def get_supported_apis() -> list[str]:
        """Get supported Azure provider APIs."""
        return get_supported_apis()

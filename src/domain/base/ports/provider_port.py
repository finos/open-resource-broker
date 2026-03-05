"""Domain port for provider operations.

This is a composite interface that combines focused provider interfaces.
Clients should depend on the specific focused interfaces they need rather than this fat interface.
"""

from abc import ABC, abstractmethod
from typing import Any

from .provider_monitoring_port import ProviderMonitoringPort
from .provider_provisioning_port import ProviderProvisioningPort
from .provider_template_port import ProviderTemplatePort


class ProviderPort(ProviderProvisioningPort, ProviderTemplatePort, ProviderMonitoringPort, ABC):
    """Composite provider port combining provisioning, template, and monitoring operations.

    This interface is provided for backward compatibility and for implementations
    that need all provider operations. New code should depend on the focused interfaces:
    - ProviderProvisioningPort: For resource provisioning/termination
    - ProviderTemplatePort: For template operations
    - ProviderMonitoringPort: For resource monitoring
    - ProviderDiscoveryPort: For infrastructure discovery (optional)

    This follows ISP by allowing clients to depend on minimal interfaces.
    """

    @abstractmethod
    def get_strategy(self, strategy_name: str) -> Any:
        """Get specific provider strategy.

        Args:
            strategy_name: Name of the strategy to retrieve

        Returns:
            Strategy instance
        """

    def discover_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover infrastructure for provider (optional).

        Args:
            provider_config: Provider configuration

        Returns:
            Dictionary containing discovered infrastructure or error message
        """
        return {"error": "Infrastructure discovery not supported"}

    def discover_infrastructure_interactive(
        self, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Discover infrastructure interactively (optional).

        Args:
            provider_config: Provider configuration

        Returns:
            Dictionary containing discovered infrastructure or error message
        """
        return {"error": "Interactive infrastructure discovery not supported"}

    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate infrastructure configuration (optional).

        Args:
            provider_config: Provider configuration to validate

        Returns:
            Dictionary containing validation results or error message
        """
        return {"error": "Infrastructure validation not supported"}

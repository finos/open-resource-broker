"""Provider discovery port - focused interface for infrastructure discovery."""

from abc import ABC, abstractmethod
from typing import Any


class ProviderDiscoveryPort(ABC):
    """Focused port for provider infrastructure discovery operations.

    This interface follows ISP by providing only discovery-related operations,
    allowing clients that only need infrastructure discovery to depend on a minimal interface.
    """

    @abstractmethod
    def discover_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover infrastructure for provider.

        Args:
            provider_config: Provider configuration

        Returns:
            Dictionary containing discovered infrastructure details
        """

    @abstractmethod
    def discover_infrastructure_interactive(
        self, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Discover infrastructure interactively.

        Args:
            provider_config: Provider configuration

        Returns:
            Dictionary containing discovered infrastructure details
        """

    @abstractmethod
    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate infrastructure configuration.

        Args:
            provider_config: Provider configuration to validate

        Returns:
            Dictionary containing validation results
        """

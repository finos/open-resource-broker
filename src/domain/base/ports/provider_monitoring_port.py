"""Provider monitoring port - focused interface for resource monitoring."""

from abc import ABC, abstractmethod
from typing import Any


class ProviderMonitoringPort(ABC):
    """Focused port for provider resource monitoring operations.

    This interface follows ISP by providing only monitoring-related operations,
    allowing clients that only need to monitor resources to depend on a minimal interface.
    """

    @abstractmethod
    def get_resource_status(self, machine_ids: list[str]) -> dict[str, Any]:
        """Get status of resources.

        Args:
            machine_ids: List of machine identifiers

        Returns:
            Dictionary mapping machine IDs to their status information
        """

    @abstractmethod
    def get_provider_info(self) -> dict[str, Any]:
        """Get provider information.

        Returns:
            Dictionary containing provider metadata and capabilities
        """

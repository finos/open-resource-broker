"""Port for provisioning orchestration operations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ProvisioningResult:
    """Result of provisioning operation."""

    success: bool
    resource_ids: list[str]
    instance_ids: list[str]
    instances: list[dict[str, Any]]
    provider_data: dict[str, Any]
    error_message: str | None = None


class ProvisioningOrchestrationPort(ABC):
    """Port for orchestrating provider provisioning operations."""

    @abstractmethod
    async def execute_provisioning(
        self,
        template: "Template",
        request: "Request",
        selection_result: "ProviderSelectionResult",
    ) -> ProvisioningResult:
        """
        Execute provisioning via selected provider.

        Args:
            template: The template to provision
            request: The request aggregate
            selection_result: The provider selection result

        Returns:
            ProvisioningResult with success status and resource information
        """

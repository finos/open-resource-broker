"""Port for request status management operations."""

from abc import ABC, abstractmethod
from typing import Any

from domain.base.ports.provisioning_orchestration_port import ProvisioningResult


class RequestStatusManagementPort(ABC):
    """Port for managing request status updates and persistence."""

    @abstractmethod
    async def update_request_from_provisioning(
        self, request: Any, provisioning_result: ProvisioningResult
    ) -> Any:
        """
        Update request status and data from provisioning results.

        Args:
            request: The request aggregate to update
            provisioning_result: The result from provisioning operation

        Returns:
            Updated request aggregate with new status and data
        """

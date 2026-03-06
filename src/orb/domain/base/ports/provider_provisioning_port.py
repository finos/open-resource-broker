"""Provider provisioning port - focused interface for resource provisioning."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orb.domain.machine.aggregate import Machine
    from orb.domain.request.aggregate import Request


class ProviderProvisioningPort(ABC):
    """Focused port for provider resource provisioning operations.

    This interface follows ISP by providing only provisioning-related operations,
    allowing clients that only need to provision/terminate resources to depend on a minimal interface.
    """

    @abstractmethod
    def provision_resources(self, request: "Request") -> "list[Machine]":
        """Provision resources based on request.

        Args:
            request: Request containing provisioning details

        Returns:
            List of provisioned machines
        """

    @abstractmethod
    def terminate_resources(self, machine_ids: list[str]) -> None:
        """Terminate resources by machine IDs.

        Args:
            machine_ids: List of machine identifiers to terminate
        """

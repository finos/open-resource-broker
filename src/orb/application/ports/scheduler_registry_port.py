"""Scheduler registry port interface."""

from abc import ABC, abstractmethod
from typing import Any


class SchedulerRegistryPort(ABC):
    """Port interface for scheduler registry operations.

    This port defines the contract for accessing scheduler providers.
    Infrastructure adapters must implement this interface.
    """

    @abstractmethod
    def get_scheduler(self, scheduler_type: str) -> Any:
        """Get a scheduler provider by type.

        Args:
            scheduler_type: The type of scheduler to retrieve

        Returns:
            The scheduler provider instance

        Raises:
            SchedulerNotFoundError: If scheduler type is not registered
        """
        ...

    @abstractmethod
    def register_scheduler(self, scheduler_type: str, scheduler: Any) -> None:
        """Register a scheduler provider.

        Args:
            scheduler_type: The type of scheduler to register
            scheduler: The scheduler provider instance
        """
        ...

    @abstractmethod
    def list_scheduler_types(self) -> list[str]:
        """List all registered scheduler types.

        Returns:
            List of scheduler type names
        """
        ...

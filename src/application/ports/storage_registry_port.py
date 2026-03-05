"""Storage registry port interface."""

from abc import ABC, abstractmethod
from typing import Any


class StorageRegistryPort(ABC):
    """Port interface for storage registry operations.

    This port defines the contract for accessing storage providers.
    Infrastructure adapters must implement this interface.
    """

    @abstractmethod
    def get_storage(self, storage_type: str) -> Any:
        """Get a storage provider by type.

        Args:
            storage_type: The type of storage to retrieve

        Returns:
            The storage provider instance

        Raises:
            StorageNotFoundError: If storage type is not registered
        """
        ...

    @abstractmethod
    def register_storage(self, storage_type: str, storage: Any) -> None:
        """Register a storage provider.

        Args:
            storage_type: The type of storage to register
            storage: The storage provider instance
        """
        ...

    @abstractmethod
    def list_storage_types(self) -> list[str]:
        """List all registered storage types.

        Returns:
            List of storage type names
        """
        ...

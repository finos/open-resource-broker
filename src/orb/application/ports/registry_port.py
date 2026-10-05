"""Generic registry port interface."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


class RegistryPort(ABC, Generic[T]):
    """Generic port interface for registry operations.

    This port defines the contract for accessing registered providers.
    Infrastructure adapters must implement this interface.
    """

    @abstractmethod
    def get(self, key: str) -> T:  # type: ignore[return]
        """Get a provider by key.

        Args:
            key: The provider key

        Returns:
            The provider instance

        Raises:
            ProviderNotFoundError: If provider is not registered
        """
        pass

    @abstractmethod
    def register(self, key: str, provider: T) -> None:
        """Register a provider.

        Args:
            key: The provider key
            provider: The provider instance
        """
        pass

    @abstractmethod
    def list_keys(self) -> list[str]:  # type: ignore[return]
        """List all registered provider keys.

        Returns:
            List of provider keys
        """
        pass

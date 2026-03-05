"""Container port for dependency injection concerns."""

from abc import ABC, abstractmethod
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


class ContainerPort(ABC):
    """Port for dependency injection container operations."""

    @abstractmethod
    def get(self, service_type: type[T]) -> T:
        """Get service instance from container."""

    @abstractmethod
    def register(self, service_type: type[T], instance: T) -> None:
        """Register service instance in container."""

    @abstractmethod
    def register_factory(self, service_type: type[T], factory_func: Callable[..., T]) -> None:
        """Register service factory in container."""

    @abstractmethod
    def register_singleton(self, service_type: type[T], factory_func: Callable[..., T]) -> None:
        """Register singleton service in container."""

    @abstractmethod
    def has(self, service_type: type[T]) -> bool:
        """Check if service is registered in container."""

    def get_optional(self, service_type: type[T]) -> Optional[T]:
        """Get service instance from container, returning None if not registered."""
        if self.has(service_type):
            return self.get(service_type)
        return None

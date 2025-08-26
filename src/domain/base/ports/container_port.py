"""Container port for dependency injection concerns."""

from abc import ABC, abstractmethod
from typing import Callable, TypeVar

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

"""Container port for dependency injection concerns."""

from abc import ABC, abstractmethod
from typing import Callable, Type, TypeVar

T = TypeVar("T")


class ContainerPort(ABC):
    """Port for dependency injection container operations."""

    @abstractmethod
    def get(self, service_type: Type[T]) -> T:
        """Get service instance from container."""

    @abstractmethod
    def register(self, service_type: Type[T], instance: T) -> None:
        """Register service instance in container."""

    @abstractmethod
    def register_factory(self, service_type: Type[T], factory_func: Callable[..., T]) -> None:
        """Register service factory in container."""

    @abstractmethod
    def register_singleton(self, service_type: Type[T], factory_func: Callable[..., T]) -> None:
        """Register singleton service in container."""

    @abstractmethod
    def has(self, service_type: Type[T]) -> bool:
        """Check if service is registered in container."""

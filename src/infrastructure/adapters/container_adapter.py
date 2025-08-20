"""Container adapter implementing ContainerPort."""

from typing import TYPE_CHECKING, Type, TypeVar

from domain.base.ports.container_port import ContainerPort

if TYPE_CHECKING:
    from infrastructure.di.container import DIContainer

T = TypeVar("T")


class ContainerAdapter(ContainerPort):
    """Adapter that implements ContainerPort using infrastructure DI container."""

    def __init__(self, container: "DIContainer") -> None:
        """Initialize with DI container instance.

        Args:
            container: DIContainer instance to wrap

        Note:
            Container is now injected as dependency instead of using get_container()
            to break circular dependency between container.py and container_adapter.py
        """
        self._container = container

    def get(self, service_type: Type[T]) -> T:
        """Get service instance from container."""
        return self._container.get(service_type)

    def register(self, service_type: Type[T], instance: T) -> None:
        """Register service instance in container."""
        self._container.register(service_type, instance)

    def register_factory(self, service_type: Type[T], factory_func) -> None:
        """Register service factory in container."""
        self._container.register_factory(service_type, factory_func)

    def register_singleton(self, service_type: Type[T], factory_func) -> None:
        """Register singleton service in container."""
        self._container.register_singleton(service_type, factory_func)

    def has(self, service_type: Type[T]) -> bool:
        """Check if service is registered in container."""
        return self._container.has(service_type)

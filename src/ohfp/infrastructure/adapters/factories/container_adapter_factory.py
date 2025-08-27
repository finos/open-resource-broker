"""Container Adapter Factory - Breaks circular dependency.

This factory creates ContainerAdapter instances without causing circular imports
between the DI container and the adapter that wraps it.

Architecture:
- Factory pattern to break circular dependency
- Lazy import to avoid module-level circular imports
- Clean separation of concerns
- Follows existing factory patterns in codebase
"""

from typing import TYPE_CHECKING

from domain.base.ports.container_port import ContainerPort

if TYPE_CHECKING:
    from infrastructure.di.container import DIContainer


class ContainerAdapterFactory:
    """Factory for creating ContainerAdapter without circular dependency.

    This factory uses lazy imports and dependency injection to create
    ContainerAdapter instances without causing circular import issues.
    """

    @staticmethod
    def create_adapter(container: "DIContainer") -> ContainerPort:
        """Create ContainerAdapter with provided container instance.

        Args:
            container: DIContainer instance to wrap

        Returns:
            ContainerPort implementation (ContainerAdapter)

        Note:
            Uses lazy import to avoid circular dependency at module level.
        """
        # Lazy import to avoid circular dependency
        from infrastructure.adapters.container_adapter import ContainerAdapter

        return ContainerAdapter(container)

    @staticmethod
    def create_adapter_factory(container: "DIContainer"):
        """Create a factory function for DI container registration.

        Args:
            container: DIContainer instance

        Returns:
            Factory function that creates ContainerAdapter

        Usage:
            container.register_singleton(ContainerPort,
                ContainerAdapterFactory.create_adapter_factory(container))
        """
        return lambda: ContainerAdapterFactory.create_adapter(container)

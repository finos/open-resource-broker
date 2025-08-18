"""Component lifecycle management."""

from abc import ABC, abstractmethod
from threading import RLock
from typing import Dict, List, Optional, Type

from infrastructure.logging.logger import get_logger


class Lifecycle(ABC):
    """
    Interface for components with lifecycle management.

    Components implementing this interface can be registered with
    the LifecycleManager for centralized initialization and cleanup.
    """

    @abstractmethod
    def initialize(self) -> None:
        """
        Initialize the component.

        This method is called by the LifecycleManager during application startup.
        It should perform any initialization tasks required by the component.
        """

    @abstractmethod
    def shutdown(self) -> None:
        """
        Shut down the component.

        This method is called by the LifecycleManager during application shutdown.
        It should release any resources held by the component.
        """


class LifecycleManager:
    """
    Manager for component lifecycles.

    This class provides centralized management of component initialization
    and cleanup. Components implementing the Lifecycle interface can be
    registered with the manager, which will ensure they are correctly
    initialized during application startup and cleaned up during shutdown.

    The manager maintains a list of registered components and initializes
    or shuts them down in the appropriate order.
    """

    _instance: Optional["LifecycleManager"] = None
    _lock = RLock()

    def __init__(self) -> None:
        """Initialize the lifecycle manager."""
        self._components: List[Lifecycle] = []
        self._component_types: Dict[Type[Lifecycle], Lifecycle] = {}
        self._logger = get_logger(__name__)

    @classmethod
    def get_instance(cls) -> "LifecycleManager":
        """
        Get the singleton instance of the lifecycle manager.

        Returns:
            LifecycleManager: The singleton instance
        """
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def register(self, component: Lifecycle) -> None:
        """
        Register a component for lifecycle management.

        Args:
            component: Component to register
        """
        component_type = type(component)

        # Check if component type is already registered
        if component_type in self._component_types:
            self._logger.debug(f"Component type {component_type.__name__} already registered")
            return

        self._components.append(component)
        self._component_types[component_type] = component
        self._logger.debug(
            f"Registered component for lifecycle management: {component_type.__name__}"
        )

    def initialize_all(self) -> None:
        """
        Initialize all registered components.

        This method is called during application startup to initialize
        all registered components in the order they were registered.
        """
        self._logger.info(f"Initializing {len(self._components)} components")
        for component in self._components:
            try:
                component.initialize()
                self._logger.debug(f"Initialized component: {component.__class__.__name__}")
            except Exception as e:
                self._logger.error(
                    f"Error initializing component { component.__class__.__name__}: { str(e)}"
                )
                import traceback

                self._logger.error(f"Initialization error details: {traceback.format_exc()}")

    def shutdown_all(self) -> None:
        """
        Shut down all registered components in reverse order.

        This method is called during application shutdown to clean up
        all registered components in the reverse order they were registered.
        """
        self._logger.info(f"Shutting down {len(self._components)} components")
        for component in reversed(self._components):
            try:
                component.shutdown()
                self._logger.debug(f"Shut down component: {component.__class__.__name__}")
            except Exception as e:
                self._logger.error(
                    f"Error shutting down component { component.__class__.__name__}: { str(e)}"
                )
                import traceback

                self._logger.error(f"Shutdown error details: {traceback.format_exc()}")

    def get_component(self, component_type: Type[Lifecycle]) -> Optional[Lifecycle]:
        """
        Get a registered component by type.

        Args:
            component_type: Type of component to get

        Returns:
            Component instance or None if not registered
        """
        return self._component_types.get(component_type)

    def reset(self) -> None:
        """
        Reset the lifecycle manager.

        This method is primarily intended for testing purposes.
        It clears all registered components.
        """
        self._components = []
        self._component_types = {}
        self._logger.debug("Reset lifecycle manager")


def get_lifecycle_manager() -> LifecycleManager:
    """
    Get the singleton instance of the lifecycle manager.

    Returns:
        LifecycleManager: The singleton instance
    """
    return LifecycleManager.get_instance()


def register_with_lifecycle_manager(component: Lifecycle) -> None:
    """
    Register a component with the lifecycle manager.

    This is a convenience function for registering components
    with the lifecycle manager.

    Args:
        component: Component to register
    """
    manager = get_lifecycle_manager()
    manager.register(component)

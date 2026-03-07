"""Registry factory for dependency injection handling."""

from typing import Any, Callable, Dict


class RegistryFactory:
    """Factory for creating registry instances with dependency injection."""

    def __init__(self):
        """Initialize the registry factory."""
        self._constructors: Dict[str, Callable] = {}
        self._dependencies: Dict[str, Dict[str, Any]] = {}

    def register_constructor(
        self,
        name: str,
        constructor: Callable,
        dependencies: Dict[str, Any] = None,  # type: ignore[assignment]
    ) -> None:
        """Register a constructor with its dependencies.

        Args:
            name: Name to register constructor under
            constructor: Constructor function/class
            dependencies: Default dependencies for constructor
        """
        self._constructors[name] = constructor
        self._dependencies[name] = dependencies or {}

    def create_instance(self, name: str, **override_kwargs) -> Any:
        """Create instance using registered constructor.

        Args:
            name: Name of registered constructor
            **override_kwargs: Override dependencies

        Returns:
            Created instance

        Raises:
            ValueError: If constructor not registered
        """
        if name not in self._constructors:
            raise ValueError(f"No constructor registered for {name}")

        constructor = self._constructors[name]
        dependencies = self._dependencies[name].copy()
        dependencies.update(override_kwargs)

        return constructor(**dependencies)

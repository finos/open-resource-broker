"""Lazy import utilities to break circular dependencies."""

import importlib
from typing import Any, Callable, Dict, Optional, Type, TypeVar

T = TypeVar("T")


class LazyImport:
    """
    Lazy import utility to break circular dependencies.

    This class provides a way to lazily import modules and access their attributes
    only when needed, which helps break circular dependencies.
    """

    def __init__(self, module_name: str, attribute_name: Optional[str] = None):
        """
        Initialize lazy import.

        Args:
            module_name: Name of the module to import
            attribute_name: Optional name of the attribute to access
        """
        self.module_name = module_name
        self.attribute_name = attribute_name
        self._module = None
        self._attribute = None

    def __call__(self, *args, **kwargs):
        """
        Call the lazily imported attribute.

        Args:
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Result of calling the attribute
        """
        if self._attribute is None:
            self._import()
        if callable(self._attribute):
            return self._attribute(*args, **kwargs)
        raise TypeError(f"Attribute '{self.attribute_name}' is not callable") from e

    def _import(self):
        """Import the module and get the attribute."""
        if self._module is None:
            self._module = importlib.import_module(self.module_name)

        if self.attribute_name:
            self._attribute = getattr(self._module, self.attribute_name)
        else:
            self._attribute = self._module

    def __getattr__(self, name: str) -> Any:
        """
        Get attribute from the lazily imported module.

        Args:
            name: Attribute name

        Returns:
            Attribute value
        """
        if self._module is None:
            self._import()

        if self.attribute_name:
            return getattr(self._attribute, name)
        else:
            return getattr(self._module, name)


# Cache for lazy singletons
_lazy_singleton_cache: Dict[Type, Any] = {}


def lazy_singleton(cls: Type[T]) -> Callable[[], T]:
    """
    Create a lazy singleton accessor function.

    This function creates a lazy accessor for a singleton class that
    only imports and instantiates the singleton when it's actually needed.

    Args:
        cls: Singleton class

    Returns:
        Function that returns the singleton instance
    """

    def get_singleton() -> T:
        """Get or create the singleton instance."""
        if cls not in _lazy_singleton_cache:
            # Import the singleton registry only when needed
            from infrastructure.patterns.singleton_registry import SingletonRegistry

            registry = SingletonRegistry.get_instance()

            # Get or create the singleton instance
            instance = registry.get(cls)
            _lazy_singleton_cache[cls] = instance

        return _lazy_singleton_cache[cls]

    return get_singleton


def reset_lazy_singletons() -> None:
    """Reset all lazy singletons."""
    _lazy_singleton_cache.clear()

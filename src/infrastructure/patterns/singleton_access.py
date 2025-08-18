"""Standard singleton access functions."""

from typing import Any, Type, TypeVar, cast

from infrastructure.logging.logger import get_logger
from infrastructure.patterns.singleton_registry import SingletonRegistry

T = TypeVar("T")


def get_singleton(singleton_class: Type[T], *args: Any, **kwargs: Any) -> T:
    """
    Get singleton instances.

    This function provides a consistent way to access singleton instances
    throughout the application. It uses the SingletonRegistry to ensure
    that only one instance of each singleton class is created and reused.

    Args:
        singleton_class: The class to get an instance of
        *args: Arguments to pass to the constructor if creating a new instance
        **kwargs: Keyword arguments to pass to the constructor if creating a new instance

    Returns:
        The singleton instance
    """
    # Try DI container first if available
    try:
        from infrastructure.di.container import get_container

        container = get_container()
        # Try to get the instance from the container
        # If it's not registered, this will raise an exception
        # which we'll catch and fall back to the registry
        return cast(T, container.get(singleton_class))
    except Exception as e:
        # Log at debug level since this is expected in some cases
        get_logger(__name__).debug(
            "DI container not available or doesn't have %s, falling back to registry: %s",
            singleton_class.__name__,
            str(e),
        )

    # Use singleton registry
    registry = SingletonRegistry.get_instance()
    return registry.get(singleton_class, *args, **kwargs)

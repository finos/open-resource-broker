"""
DI framework decorators and utilities — relocated from domain/base.

This module is the canonical location for the injectable marker decorator,
singleton/lazy/requires/factory decorators, CQRS handler decorators, and the
associated InjectableMetadata helper.  The domain layer previously hosted these
but they are pure infrastructure-wiring concerns with no domain meaning.

ContainerPort stays in domain/base/ports as the legitimate domain abstraction.
"""

import inspect
from typing import Any, Callable, Generic, Optional, TypeVar

T = TypeVar("T")


class InjectableMetadata:
    """Metadata for injectable classes."""

    def __init__(
        self,
        auto_wire: bool = True,
        singleton: bool = False,
        dependencies: Optional[list[type]] = None,
        factory: Optional[Callable] = None,
        lazy: bool = False,
    ) -> None:
        """Initialize the instance."""
        self.auto_wire = auto_wire
        self.singleton = singleton
        self.dependencies = dependencies or []
        self.factory = factory
        self.lazy = lazy

    def to_dict(self) -> dict[str, Any]:
        """Convert metadata to dictionary."""
        return {
            "auto_wire": self.auto_wire,
            "singleton": self.singleton,
            "dependencies": self.dependencies,
            "factory": self.factory,
            "lazy": self.lazy,
        }


def injectable(cls: type[T]) -> type[T]:
    """
    Mark a class as injectable (lightweight marker for DI container auto-wiring).

    Sets ``_injectable = True`` and populates ``_injectable_metadata`` with
    constructor parameter analysis.  The DI container reads the ``_injectable``
    flag when deciding whether to auto-register a class.

    This is a **marker** decorator — it does not replace ``__init__``.

    Args:
        cls: The class to make injectable.

    Returns:
        The same class with injectable metadata attached.

    Example:
        @injectable
        class UserService:
            def __init__(self, repository: UserRepository) -> None:
                self.repository = repository
    """
    cls._injectable = True  # type: ignore[attr-defined]

    metadata = InjectableMetadata(
        auto_wire=True,
        singleton=getattr(cls, "_singleton", False),
        dependencies=getattr(cls, "_dependencies", []),
        factory=getattr(cls, "_factory", None),
        lazy=getattr(cls, "_lazy", False),
    )

    cls._injectable_metadata = metadata  # type: ignore[attr-defined]

    if hasattr(cls, "__init__"):
        cls._original_init = cls.__init__  # type: ignore[attr-defined]

        sig = inspect.signature(cls.__init__)
        dependencies = []
        for param_name, param in sig.parameters.items():
            if param_name != "self" and param.annotation != inspect.Parameter.empty:
                dependencies.append(param.annotation)

        metadata.dependencies = dependencies

    return cls


def singleton(cls: type[T]) -> type[T]:
    """
    Mark class as singleton for DI container.

    Args:
        cls: The class to mark as singleton.

    Returns:
        The decorated class with singleton metadata.
    """
    cls._singleton = True  # type: ignore[attr-defined]
    return cls


def requires(*dependencies: type) -> Callable[[type[T]], type[T]]:
    """
    Specify explicit dependencies that cannot be inferred from type hints.

    Args:
        *dependencies: The dependency types required.

    Returns:
        Decorator function.
    """

    def decorator(cls: type[T]) -> type[T]:
        """Apply requires decorator to the class."""
        cls._dependencies = list(dependencies)  # type: ignore[attr-defined]
        return cls

    return decorator


def factory(factory_func: Callable[[], T]) -> Callable[[type[T]], type[T]]:
    """
    Specify custom factory function for dependency creation.

    Args:
        factory_func: Function that creates instances.

    Returns:
        Decorator function.
    """

    def decorator(cls: type[T]) -> type[T]:
        """Attach factory function to class."""
        cls._factory = factory_func  # type: ignore[attr-defined]
        return cls

    return decorator


def lazy(cls: type[T]) -> type[T]:
    """
    Mark dependency for lazy initialization.

    Args:
        cls: The class to mark as lazy.

    Returns:
        The decorated class with lazy metadata.
    """
    cls._lazy = True  # type: ignore[attr-defined]
    return cls


# CQRS-Specific Decorators


def command_handler(command_type: type) -> Callable[[type[T]], type[T]]:
    """
    Mark class as CQRS command handler.

    Combines @injectable with command handler metadata.

    Args:
        command_type: The command type this handler processes.

    Returns:
        Decorator function.
    """

    def decorator(cls: type[T]) -> type[T]:
        """Register class as command handler."""
        cls._command_type = command_type  # type: ignore[attr-defined]
        cls._handler_type = "command"  # type: ignore[attr-defined]
        cls._cqrs_handler = True  # type: ignore[attr-defined]
        return injectable(cls)

    return decorator


def query_handler(query_type: type) -> Callable[[type[T]], type[T]]:
    """
    Mark class as CQRS query handler.

    Combines @injectable with query handler metadata.

    Args:
        query_type: The query type this handler processes.

    Returns:
        Decorator function.
    """

    def decorator(cls: type[T]) -> type[T]:
        """Register class as query handler."""
        cls._query_type = query_type  # type: ignore[attr-defined]
        cls._handler_type = "query"  # type: ignore[attr-defined]
        cls._cqrs_handler = True  # type: ignore[attr-defined]
        return injectable(cls)

    return decorator


def event_handler(event_type: type) -> Callable[[type[T]], type[T]]:
    """
    Mark class as domain event handler.

    Combines @injectable with event handler metadata.

    Args:
        event_type: The event type this handler processes.

    Returns:
        Decorator function.
    """

    def decorator(cls: type[T]) -> type[T]:
        """Register class as event handler."""
        cls._event_type = event_type  # type: ignore[attr-defined]
        cls._handler_type = "event"  # type: ignore[attr-defined]
        cls._cqrs_handler = True  # type: ignore[attr-defined]
        return injectable(cls)

    return decorator


# Utility Functions


def is_injectable(cls: type) -> bool:
    """
    Check if class is marked as injectable.

    Args:
        cls: The class to check.

    Returns:
        True if class is injectable, False otherwise.
    """
    return hasattr(cls, "_injectable") and cls._injectable


def get_injectable_metadata(cls: type) -> Optional[InjectableMetadata]:
    """
    Get injectable metadata for class.

    Args:
        cls: The class to get metadata for.

    Returns:
        InjectableMetadata if class is injectable, None otherwise.
    """
    if hasattr(cls, "_injectable_metadata"):
        metadata: InjectableMetadata = cls._injectable_metadata
        return metadata
    return None


def is_singleton(cls: type) -> bool:
    """
    Check if class is marked as singleton.

    Args:
        cls: The class to check.

    Returns:
        True if class is singleton, False otherwise.
    """
    return hasattr(cls, "_singleton") and cls._singleton


def is_cqrs_handler(cls: type) -> bool:
    """
    Check if class is a CQRS handler.

    Args:
        cls: The class to check.

    Returns:
        True if class is a CQRS handler, False otherwise.
    """
    return hasattr(cls, "_cqrs_handler") and cls._cqrs_handler


def get_handler_type(cls: type) -> Optional[str]:
    """
    Get CQRS handler type.

    Args:
        cls: The class to check.

    Returns:
        Handler type ('command', 'query', 'event') or None.
    """
    if hasattr(cls, "_handler_type"):
        handler_type: str = cls._handler_type
        return handler_type
    return None


def get_dependencies(cls: type) -> list[type]:
    """
    Get explicit dependencies for class.

    Args:
        cls: The class to get dependencies for.

    Returns:
        List of dependency types.
    """
    if hasattr(cls, "_dependencies"):
        dependencies: list[type] = cls._dependencies
        return dependencies

    metadata = get_injectable_metadata(cls)
    if metadata:
        return metadata.dependencies

    return []


# Optional Dependency Helper


class OptionalDependency(Generic[T]):
    """
    Wrapper for optional dependencies.

    Use this to mark dependencies as optional in constructor parameters.
    """

    def __init__(self, dependency_type: type[T]) -> None:
        """Initialize with the wrapped dependency type."""
        self.dependency_type = dependency_type

    def __repr__(self) -> str:
        """Return string representation."""
        return f"OptionalDependency({self.dependency_type})"


def optional_dependency(dependency_type: type[T]) -> OptionalDependency[T]:
    """
    Mark dependency as optional.

    Optional dependencies are injected if available, with no error if absent.

    Args:
        dependency_type: The dependency type.

    Returns:
        OptionalDependency wrapper.
    """
    return OptionalDependency(dependency_type)


def get_injectable_info(cls: type) -> dict[str, Any]:
    """
    Get injectable information for class (backward compatibility).

    Args:
        cls: The class to get info for.

    Returns:
        Dictionary with injectable information.
    """
    metadata = get_injectable_metadata(cls)
    if metadata:
        return metadata.to_dict()

    return {
        "injectable": is_injectable(cls),
        "singleton": is_singleton(cls),
        "dependencies": get_dependencies(cls),
        "cqrs_handler": is_cqrs_handler(cls),
        "handler_type": get_handler_type(cls),
    }

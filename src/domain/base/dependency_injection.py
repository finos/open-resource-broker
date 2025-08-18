"""
Domain Dependency Injection - Core DI abstractions and decorators.

This module provides the domain-level dependency injection contracts and decorators,
treating DI as a domain concern for how business objects are wired together.
This approach maintains Clean Architecture by allowing Application layer to depend
on Domain layer DI contracts rather than Infrastructure implementations.

Key Principles:
- DI is a domain concern (how objects are wired is business logic)
- Infrastructure implements domain DI contracts
- Application layer uses domain DI abstractions
- Preserves @injectable decorator pattern while fixing architectural violations
"""

import inspect
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Generic, List, Optional, Type, TypeVar, Union

T = TypeVar("T")


class DependencyInjectionPort(ABC):
    """
    Domain port for dependency injection operations.

    This port defines the contract that infrastructure DI containers
    must implement, following the Port-Adapter pattern.
    """

    @abstractmethod
    def get(self, cls: Type[T]) -> T:
        """
        Resolve dependency by type.

        Args:
            cls: The class type to resolve

        Returns:
            Instance of the requested type

        Raises:
            DependencyResolutionError: If dependency cannot be resolved
        """

    @abstractmethod
    def register(self, cls: Type[T], instance_or_factory: Union[T, Callable[[], T]]) -> None:
        """
        Register dependency in container.

        Args:
            cls: The class type to register
            instance_or_factory: Instance or factory function
        """

    @abstractmethod
    def register_singleton(
        self, cls: Type[T], instance_or_factory: Union[T, Callable[[], T]]
    ) -> None:
        """
        Register dependency as singleton.

        Args:
            cls: The class type to register
            instance_or_factory: Instance or factory function
        """

    @abstractmethod
    def is_registered(self, cls: Type[T]) -> bool:
        """
        Check if type is registered in container.

        Args:
            cls: The class type to check

        Returns:
            True if registered, False otherwise
        """


class DependencyResolutionError(Exception):
    """Raised when dependency cannot be resolved."""


# Injectable Metadata Classes


class InjectableMetadata:
    """Metadata for injectable classes."""

    def __init__(
        self,
        auto_wire: bool = True,
        singleton: bool = False,
        dependencies: Optional[List[Type]] = None,
        factory: Optional[Callable] = None,
        lazy: bool = False,
    ) -> None:
        """Initialize the instance."""
        self.auto_wire = auto_wire
        self.singleton = singleton
        self.dependencies = dependencies or []
        self.factory = factory
        self.lazy = lazy

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary."""
        return {
            "auto_wire": self.auto_wire,
            "singleton": self.singleton,
            "dependencies": self.dependencies,
            "factory": self.factory,
            "lazy": self.lazy,
        }


# Core DI Decorators


def injectable(cls: Type[T]) -> Type[T]:
    """
    Domain decorator for marking classes as injectable.

    This decorator marks classes for automatic dependency injection
    without coupling to infrastructure implementation. It preserves
    the existing @injectable pattern while moving it to the domain layer.

    Features:
    - Automatic dependency resolution
    - Constructor parameter analysis
    - Type hint support
    - Optional dependency handling

    Args:
        cls: The class to make injectable

    Returns:
        The decorated class with injectable metadata

    Example:
        @injectable
        class UserService:
            def __init__(self, repository: UserRepository) -> None:
                self.repository = repository
    """
    # Preserve existing functionality
    cls._injectable = True

    # Additional metadata
    metadata = InjectableMetadata(
        auto_wire=True,
        singleton=getattr(cls, "_singleton", False),
        dependencies=getattr(cls, "_dependencies", []),
        factory=getattr(cls, "_factory", None),
        lazy=getattr(cls, "_lazy", False),
    )

    cls._injectable_metadata = metadata

    # Store original __init__ for dependency analysis
    if hasattr(cls, "__init__"):
        cls._original_init = cls.__init__

        # Analyze constructor parameters for auto-wiring
        sig = inspect.signature(cls.__init__)
        dependencies = []

        for param_name, param in sig.parameters.items():
            if param_name != "self" and param.annotation != inspect.Parameter.empty:
                dependencies.append(param.annotation)

        metadata.dependencies = dependencies

    return cls


def singleton(cls: Type[T]) -> Type[T]:
    """
    Mark class as singleton for DI container.

    This decorator marks a class to be registered as a singleton,
    meaning only one instance will be created and reused.

    Args:
        cls: The class to mark as singleton

    Returns:
        The decorated class with singleton metadata

    Example:
        @singleton
        @injectable
        class ConfigurationService:
            pass
    """
    cls._singleton = True
    return cls


def requires(*dependencies: Type) -> Callable[[Type[T]], Type[T]]:
    """
    Specify explicit dependencies.

    Use this decorator when you need to explicitly specify dependencies
    that cannot be inferred from constructor parameters.

    Args:
        *dependencies: The dependency types required

    Returns:
        Decorator function

    Example:
        @requires(UserRepository, EmailService)
        @injectable
        class UserService:
            pass
    """

    def decorator(cls: Type[T]) -> Type[T]:
        """Apply requires decorator to the class."""
        cls._dependencies = list(dependencies)
        return cls

    return decorator


def factory(factory_func: Callable[[], T]) -> Callable[[Type[T]], Type[T]]:
    """
    Specify custom factory function for dependency creation.

    Args:
        factory_func: Function that creates instances

    Returns:
        Decorator function

    Example:
        def create_database_connection():
            return DatabaseConnection(config.db_url)

        @factory(create_database_connection)
        @injectable
        class DatabaseService:
            pass
    """

    def decorator(cls: Type[T]) -> Type[T]:
        """Attach factory function to class."""
        cls._factory = factory_func
        return cls

    return decorator


def lazy(cls: Type[T]) -> Type[T]:
    """
    Mark dependency for lazy initialization.

    Lazy dependencies are only created when first accessed,
    which can help with circular dependencies and performance.

    Args:
        cls: The class to mark as lazy

    Returns:
        The decorated class with lazy metadata
    """
    cls._lazy = True
    return cls


# CQRS-Specific Decorators


def command_handler(command_type: Type) -> Callable[[Type[T]], Type[T]]:
    """
    Mark class as CQRS command handler.

    This decorator combines @injectable with command handler metadata,
    making it easy to register command handlers in the DI container.

    Args:
        command_type: The command type this handler processes

    Returns:
        Decorator function

    Example:
        @command_handler(CreateUserCommand)
        class CreateUserHandler:
            def handle(self, command: CreateUserCommand) -> None:
                pass
    """

    def decorator(cls: Type[T]) -> Type[T]:
        """Register class as command handler."""
        cls._command_type = command_type
        cls._handler_type = "command"
        cls._cqrs_handler = True
        return injectable(cls)

    return decorator


def query_handler(query_type: Type) -> Callable[[Type[T]], Type[T]]:
    """
    Mark class as CQRS query handler.

    This decorator combines @injectable with query handler metadata,
    making it easy to register query handlers in the DI container.

    Args:
        query_type: The query type this handler processes

    Returns:
        Decorator function

    Example:
        @query_handler(GetUserQuery)
        class GetUserHandler:
            def handle(self, query: GetUserQuery) -> None:
                pass
    """

    def decorator(cls: Type[T]) -> Type[T]:
        """Register class as query handler."""
        cls._query_type = query_type
        cls._handler_type = "query"
        cls._cqrs_handler = True
        return injectable(cls)

    return decorator


def event_handler(event_type: Type) -> Callable[[Type[T]], Type[T]]:
    """
    Mark class as domain event handler.

    This decorator combines @injectable with event handler metadata,
    making it easy to register event handlers in the DI container.

    Args:
        event_type: The event type this handler processes

    Returns:
        Decorator function

    Example:
        @event_handler(UserCreatedEvent)
        class UserCreatedHandler:
            def handle(self, event: UserCreatedEvent) -> None:
                pass
    """

    def decorator(cls: Type[T]) -> Type[T]:
        """Register class as event handler."""
        cls._event_type = event_type
        cls._handler_type = "event"
        cls._cqrs_handler = True
        return injectable(cls)

    return decorator


# Utility Functions


def is_injectable(cls: Type) -> bool:
    """
    Check if class is marked as injectable.

    Args:
        cls: The class to check

    Returns:
        True if class is injectable, False otherwise
    """
    return hasattr(cls, "_injectable") and cls._injectable


def get_injectable_metadata(cls: Type) -> Optional[InjectableMetadata]:
    """
    Get injectable metadata for class.

    Args:
        cls: The class to get metadata for

    Returns:
        InjectableMetadata if class is injectable, None otherwise
    """
    if hasattr(cls, "_injectable_metadata"):
        metadata: InjectableMetadata = cls._injectable_metadata
        return metadata
    return None


def is_singleton(cls: Type) -> bool:
    """
    Check if class is marked as singleton.

    Args:
        cls: The class to check

    Returns:
        True if class is singleton, False otherwise
    """
    return hasattr(cls, "_singleton") and cls._singleton


def is_cqrs_handler(cls: Type) -> bool:
    """
    Check if class is a CQRS handler.

    Args:
        cls: The class to check

    Returns:
        True if class is a CQRS handler, False otherwise
    """
    return hasattr(cls, "_cqrs_handler") and cls._cqrs_handler


def get_handler_type(cls: Type) -> Optional[str]:
    """
    Get CQRS handler type.

    Args:
        cls: The class to check

    Returns:
        Handler type ('command', 'query', 'event') or None
    """
    if hasattr(cls, "_handler_type"):
        handler_type: str = cls._handler_type
        return handler_type
    return None


def get_dependencies(cls: Type) -> List[Type]:
    """
    Get explicit dependencies for class.

    Args:
        cls: The class to get dependencies for

    Returns:
        List of dependency types
    """
    if hasattr(cls, "_dependencies"):
        dependencies: List[Type] = cls._dependencies
        return dependencies

    # Try to get from metadata
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

    def __init__(self, dependency_type: Type[T]) -> None:
        self.dependency_type = dependency_type

    def __repr__(self):
        return f"OptionalDependency({self.dependency_type})"


def optional_dependency(dependency_type: Type[T]) -> OptionalDependency[T]:
    """
    Mark dependency as optional.

    Optional dependencies will be injected if available,
    but won't cause errors if not registered.

    Args:
        dependency_type: The dependency type

    Returns:
        OptionalDependency wrapper

    Example:
        @injectable
        class UserService:
            def __init__(
                self,
                repository: UserRepository,
                cache: OptionalDependency[CacheService] = optional_dependency(CacheService)
            ):
                self.repository = repository
                self.cache = cache
    """
    return OptionalDependency(dependency_type)


# Backward Compatibility


# Preserve existing function names for compatibility
def get_injectable_info(cls: Type) -> Dict[str, Any]:
    """
    Get injectable information for class (backward compatibility).

    Args:
        cls: The class to get info for

    Returns:
        Dictionary with injectable information
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

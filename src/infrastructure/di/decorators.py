"""
Injectable decorator for automatic dependency injection.

This decorator enables classes to automatically resolve their dependencies
from the DI container without requiring explicit factory functions.
"""

import inspect
import logging
from functools import wraps
from typing import Any, Dict, Type, TypeVar, Union, get_args, get_origin, get_type_hints

T = TypeVar("T")

# Logger for decorator operations
logger = logging.getLogger(__name__)


def injectable(cls: Type[T]) -> Type[T]:
    """
    Mark a class as injectable with automatic dependency resolution.

    This decorator:
    1. Analyzes the class constructor's type hints
    2. Automatically resolves dependencies from the DI container
    3. Handles Optional[T] types by trying to resolve T, falling back to None
    4. Preserves original constructor behavior for manual instantiation

    Usage:
        @injectable
        class MyService:
            def __init__(self, logger: LoggingPort, config: Optional[ConfigPort] = None) -> None:
                self.logger = logger
                self.config = config

    Args:
        cls: The class to make injectable

    Returns:
        The same class with dependency injection
    """
    # Store original constructor
    original_init = cls.__init__

    # Get type hints from constructor
    try:
        hints = get_type_hints(original_init)
    except Exception as e:
        logger.warning("Could not get type hints for %s: %s", cls.__name__, e)
        hints = {}

    # Get constructor signature
    try:
        sig = inspect.signature(original_init)
    except Exception as e:
        logger.warning("Could not get signature for %s: %s", cls.__name__, e)
        return cls

    @wraps(original_init)
    def dependency_init(self, *args, **kwargs):
        """Constructor with automatic dependency resolution."""
        # If positional arguments are provided, use original constructor directly
        if args:
            return original_init(self, *args, **kwargs)

        resolved_kwargs = {}

        # Process each parameter
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue

            # If parameter was explicitly provided, use it
            if param_name in kwargs:
                resolved_kwargs[param_name] = kwargs[param_name]
                continue

            # Try to resolve from DI container
            if param_name in hints:
                annotation = hints[param_name]
                resolved_value = _resolve_dependency(annotation, param, cls.__name__, param_name)
                if resolved_value is not None:
                    resolved_kwargs[param_name] = resolved_value
                elif param.default != inspect.Parameter.empty:
                    # Use default value if resolution failed and default exists
                    resolved_kwargs[param_name] = param.default
                # If no default and resolution failed, let original constructor handle
                # it
            elif param.default != inspect.Parameter.empty:
                # No type hint but has default - use default
                resolved_kwargs[param_name] = param.default

        # Call original constructor with resolved dependencies
        try:
            original_init(self, **resolved_kwargs)
        except Exception as e:
            logger.error("Failed to initialize %s with resolved dependencies: %s", cls.__name__, e)
            logger.debug("Resolved kwargs: %s", resolved_kwargs)
            raise

    # Replace constructor
    cls.__init__ = dependency_init
    cls._injectable = True
    cls._original_init = original_init

    logger.debug("Made %s injectable", cls.__name__)
    return cls


def _is_primitive_type(annotation: Type) -> bool:
    """Check if a type annotation represents a primitive type that shouldn't be resolved from DI."""
    primitive_types = {str, int, float, bool, bytes, type(None)}

    # Check direct primitive types
    if annotation in primitive_types:
        return True

    # Check if it's a generic type with primitive origin (like List[str])
    origin = get_origin(annotation)
    if origin in primitive_types:
        return True

    # Handle Any type (used for variadic parameters)
    if annotation is Any:
        return True

    return False


def _resolve_dependency(
    annotation: Type, param: inspect.Parameter, class_name: str, param_name: str
) -> Any:
    """
    Resolve a single dependency from the DI container.

    Args:
        annotation: The type annotation
        param: The parameter object
        class_name: Name of the class being constructed
        param_name: Name of the parameter

    Returns:
        Resolved dependency or None if resolution failed
    """
    try:
        # Import here to avoid circular imports
        from infrastructure.di.container import get_container

        container = get_container()

        # Handle Optional[T] types
        if _is_optional_type(annotation):
            inner_type = _extract_optional_inner_type(annotation)

            # Don't try to resolve primitive types from DI container
            if _is_primitive_type(inner_type):
                logger.debug(
                    "Skipping primitive type resolution for %s: %s in %s",
                    param_name,
                    inner_type.__name__,
                    class_name,
                )
                return param.default if param.default != inspect.Parameter.empty else None

            try:
                return container.get(inner_type)
            except Exception as e:
                logger.debug(
                    "Could not resolve optional dependency %s: %s for %s: %s",
                    param_name,
                    inner_type.__name__,
                    class_name,
                    e,
                )
                return param.default if param.default != inspect.Parameter.empty else None

        # Don't try to resolve primitive types from DI container
        if _is_primitive_type(annotation):
            logger.debug(
                "Skipping primitive type resolution for %s: %s in %s",
                param_name,
                annotation.__name__,
                class_name,
            )
            return param.default if param.default != inspect.Parameter.empty else None

        # Handle regular types
        try:
            return container.get(annotation)
        except Exception as e:
            logger.debug(
                "Could not resolve dependency %s: %s for %s: %s",
                param_name,
                annotation.__name__,
                class_name,
                e,
            )
            return None

    except Exception as e:
        logger.warning("Error resolving dependency %s for %s: %s", param_name, class_name, e)
        return None


def _is_optional_type(annotation: Type) -> bool:
    """Check if a type annotation represents Optional[T]."""
    origin = get_origin(annotation)
    if origin is Union:
        args = get_args(annotation)
        # Optional[T] is Union[T, None]
        return len(args) == 2 and type(None) in args
    return False


def _extract_optional_inner_type(annotation: Type) -> Type:
    """Extract T from Optional[T]."""
    args = get_args(annotation)
    return next(arg for arg in args if arg is not type(None))


def is_injectable(cls: Type) -> bool:
    """Check if a class has been marked as injectable."""
    return hasattr(cls, "_injectable") and cls._injectable


def get_injectable_info(cls: Type) -> Dict[str, Any]:
    """Get information about an injectable class."""
    if not is_injectable(cls):
        return {}

    try:
        hints = get_type_hints(cls._original_init)
        sig = inspect.signature(cls._original_init)

        dependencies = {}
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue

            if param_name in hints:
                annotation = hints[param_name]
                dependencies[param_name] = {
                    "type": annotation,
                    "optional": _is_optional_type(annotation),
                    "has_default": param.default != inspect.Parameter.empty,
                    "default}": (
                        param.default if param.default != inspect.Parameter.empty else None
                    ),
                }

        return {
            "class_name": cls.__name__,
            "dependencies": dependencies,
            "total_dependencies": len(dependencies),
        }
    except Exception as e:
        logger.warning("Could not get injectable info for %s: %s", cls.__name__, e)
        return {"class_name": cls.__name__, "error": str(e)}

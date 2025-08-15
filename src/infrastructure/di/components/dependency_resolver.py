"""Dependency resolution engine for DI container."""

import inspect
import logging
import threading
import typing
from contextlib import suppress
from typing import Any, Callable, Dict, Optional, Set, Type, TypeVar, get_type_hints

from domain.base.dependency_injection import get_injectable_metadata, is_injectable
from domain.base.di_contracts import DependencyRegistration, DILifecycle, DIScope
from infrastructure.di.exceptions import (
    CircularDependencyError,
    DependencyResolutionError,
    FactoryError,
    InstantiationError,
    UnregisteredDependencyError,
    UntypedParameterError,
)

T = TypeVar("T")
logger = logging.getLogger(__name__)


class DependencyResolver:
    """Handles dependency resolution and instance creation."""

    def __init__(self, service_registry, cqrs_registry):
        """Initialize the instance."""
        self._service_registry = service_registry
        self._cqrs_registry = cqrs_registry
        self._lock = threading.RLock()
        self._resolution_cache: Dict[Type, Any] = {}

    def resolve(
        self,
        cls: Type[T],
        parent_type: Optional[Type] = None,
        dependency_chain: Optional[Set[Type]] = None,
    ) -> T:
        """
        Resolve a dependency and create an instance.

        Args:
            cls: Type to resolve
            parent_type: Parent type for context
            dependency_chain: Chain of dependencies to detect circular references

        Returns:
            Instance of the requested type

        Raises:
            DependencyResolutionError: If dependency cannot be resolved
            CircularDependencyError: If circular dependency detected
        """
        if dependency_chain is None:
            dependency_chain = set()

        # Check for circular dependencies
        if cls in dependency_chain:
            chain_str = " -> ".join([c.__name__ for c in dependency_chain]) + f" -> {cls.__name__}"
            raise CircularDependencyError(f"Circular dependency detected: {chain_str}")

        # Add current class to dependency chain
        new_chain = dependency_chain | {cls}

        try:
            with self._lock:
                # Check if it's a singleton and already cached
                registration = self._service_registry.get_registration(cls)
                if registration and registration.scope == DIScope.SINGLETON:
                    cached_instance = self._service_registry.get_singleton_instance(cls)
                    if cached_instance is not None:
                        return cached_instance

                # Create new instance
                instance = self._create_instance(cls, new_chain)

                # Cache singleton instances
                if registration and registration.scope == DIScope.SINGLETON:
                    self._service_registry.set_singleton_instance(cls, instance)

                return instance

        except Exception as e:
            if isinstance(e, (DependencyResolutionError, CircularDependencyError)):
                raise
            else:
                raise DependencyResolutionError(cls, f"Failed to resolve {cls.__name__}: {str(e)}")

    def _create_instance(self, cls: Type[T], dependency_chain: Set[Type]) -> T:
        """Create an instance of the specified type."""
        try:
            # Get registration
            registration = self._service_registry.get_registration(cls)

            if registration:
                return self._create_from_registration(registration, dependency_chain)
            else:
                # Try to create instance directly if it's a concrete class
                return self._create_direct_instance(cls, dependency_chain)

        except Exception as e:
            if isinstance(e, (DependencyResolutionError, CircularDependencyError)):
                raise
            else:
                raise InstantiationError(
                    cls, f"Failed to create instance of {cls.__name__}: {str(e)}"
                )

    def _create_from_registration(
        self, registration: DependencyRegistration, dependency_chain: Set[Type]
    ) -> Any:
        """Create instance from registration."""
        if registration.instance is not None:
            return registration.instance

        if registration.factory is not None:
            try:
                # Call factory with container - this is the working pattern from the monolithic version
                # Factory functions expect the container as their parameter: lambda c:
                # SomeClass(c.get(...))
                container = self._get_container_instance()
                return registration.factory(container)
            except Exception as e:
                raise FactoryError(
                    registration.dependency_type,
                    f"Factory failed for { registration.dependency_type.__name__}: { str(e)}",
                )

        if registration.implementation_type is not None:
            return self._create_direct_instance(registration.implementation_type, dependency_chain)

        # Fallback to dependency type
        return self._create_direct_instance(registration.dependency_type, dependency_chain)

    def _create_direct_instance(self, cls: Type[T], dependency_chain: Set[Type]) -> T:
        """Create instance directly from class."""
        try:
            # Check if class is injectable and auto-register if needed
            if is_injectable(cls) and not self._service_registry.is_registered(cls):
                self._auto_register_injectable_class(cls)

            # Get constructor parameters
            constructor_params = self._resolve_constructor_parameters(cls, dependency_chain)

            # Create instance
            instance = cls(**constructor_params)

            logger.debug(f"Created instance of {cls.__name__}")
            return instance

        except Exception as e:
            if isinstance(e, (DependencyResolutionError, CircularDependencyError)):
                raise
            else:
                raise InstantiationError(cls, f"Failed to instantiate {cls.__name__}: {str(e)}")

    def _auto_register_injectable_class(self, cls: Type) -> None:
        """Auto-register an injectable class."""
        try:
            metadata = get_injectable_metadata(cls)
            if metadata:
                # Create registration based on metadata
                scope = DIScope.SINGLETON if metadata.singleton else DIScope.TRANSIENT
                registration = DependencyRegistration(
                    dependency_type=cls,
                    implementation_type=cls,
                    scope=scope,
                    lifecycle=DILifecycle.LAZY if metadata.lazy else DILifecycle.EAGER,
                    dependencies=metadata.dependencies,
                    factory=metadata.factory,
                )
                self._service_registry.register(registration)
                logger.debug(f"Auto-registered injectable class: {cls.__name__}")
            else:
                # Fallback for old-style @injectable without metadata
                self._service_registry.register_injectable_class(cls)
                logger.debug(f"Auto-registered legacy injectable class: {cls.__name__}")
        except Exception as e:
            logger.warning(f"Failed to auto-register injectable class {cls.__name__}: {e}")

    def _resolve_constructor_parameters(
        self, cls: Type, dependency_chain: Set[Type]
    ) -> Dict[str, Any]:
        """Resolve constructor parameters for a class."""
        try:
            # Get constructor signature
            signature = inspect.signature(cls.__init__)
            parameters = {}

            # Get type hints
            type_hints = get_type_hints(cls.__init__)

            for param_name, param in signature.parameters.items():
                if param_name == "self":
                    continue

                # Skip *args and **kwargs parameters - they can't be resolved as
                # dependencies
                if param.kind in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                ):
                    continue

                # Get parameter type
                param_type = type_hints.get(param_name, param.annotation)

                if param_type == inspect.Parameter.empty:
                    if param.default != inspect.Parameter.empty:
                        # Has default value, skip
                        continue
                    else:
                        raise UntypedParameterError(cls, param_name)

                # Handle string annotations
                if isinstance(param_type, str):
                    param_type = self._resolve_string_annotation(param_type, cls)

                # Skip primitive types that can't be resolved as dependencies
                if self._is_primitive_type(param_type):
                    if param.default != inspect.Parameter.empty:
                        # Has default value, skip
                        continue
                    else:
                        # Primitive type without default - can't resolve
                        raise DependencyResolutionError(
                            cls,
                            f"Cannot resolve primitive type '{param_type.__name__}' for parameter '{param_name}'. "
                            f"Primitive types must have default values or be provided explicitly.",
                        )

                # Resolve dependency
                if param.default == inspect.Parameter.empty:
                    # Required parameter
                    parameters[param_name] = self.resolve(param_type, cls, dependency_chain)
                else:
                    # Optional parameter - try to resolve, use default if not available
                    with suppress(DependencyResolutionError, UnregisteredDependencyError):
                        parameters[param_name] = self.resolve(param_type, cls, dependency_chain)

            return parameters

        except Exception as e:
            if isinstance(
                e,
                (
                    DependencyResolutionError,
                    CircularDependencyError,
                    UntypedParameterError,
                ),
            ):
                raise
            else:
                raise DependencyResolutionError(
                    cls,
                    f"Failed to resolve constructor parameters for { cls.__name__}: { str(e)}",
                )

    def _resolve_function_parameters(
        self, func: Callable, dependency_chain: Set[Type]
    ) -> Dict[str, Any]:
        """Resolve function parameters for factory functions."""
        try:
            signature = inspect.signature(func)
            parameters = {}

            # Get type hints
            type_hints = get_type_hints(func)

            for param_name, param in signature.parameters.items():
                # Get parameter type
                param_type = type_hints.get(param_name, param.annotation)

                if param_type == inspect.Parameter.empty:
                    if param.default != inspect.Parameter.empty:
                        # Has default value, skip
                        continue
                    else:
                        # For factory functions, untyped parameters are typically the container
                        # This is a common pattern: lambda c: SomeClass(c.get(Dependency))
                        # We'll provide the container instance for untyped parameters
                        parameters[param_name] = self._get_container_instance()
                        continue

                # Handle string annotations
                if isinstance(param_type, str):
                    param_type = self._resolve_string_annotation(param_type, type(func))

                # Resolve dependency
                if param.default == inspect.Parameter.empty:
                    # Required parameter
                    parameters[param_name] = self.resolve(param_type, None, dependency_chain)
                else:
                    # Optional parameter
                    with suppress(DependencyResolutionError, UnregisteredDependencyError):
                        parameters[param_name] = self.resolve(param_type, None, dependency_chain)

            return parameters

        except Exception as e:
            if isinstance(
                e,
                (
                    DependencyResolutionError,
                    CircularDependencyError,
                    UntypedParameterError,
                ),
            ):
                raise
            else:
                raise DependencyResolutionError(
                    type(func), f"Failed to resolve factory parameters: {str(e)}"
                )

    def _resolve_string_annotation(self, annotation: str, context_class: Type) -> Type:
        """Resolve string type annotations to actual types."""
        try:
            # Get the module where the context class is defined
            module = inspect.getmodule(context_class)
            if module is None:
                raise ValueError(f"Could not get module for {context_class}")

            # Try to resolve the annotation in the module's namespace
            module_globals = getattr(module, "__dict__", {})

            # Handle forward references and complex types
            if annotation in module_globals:
                return module_globals[annotation]

            # Try to safely evaluate the annotation
            try:
                # For simple literals, use ast.literal_eval for safety
                import ast

                try:
                    return ast.literal_eval(annotation)
                except (ValueError, SyntaxError):
                    # For complex type annotations, use getattr with module resolution
                    if hasattr(typing, annotation):
                        return getattr(typing, annotation)
                    # Try to resolve from module globals safely
                    parts = annotation.split(".")
                    obj = module_globals.get(parts[0])
                    if obj is not None:
                        for part in parts[1:]:
                            obj = getattr(obj, part, None)
                            if obj is None:
                                break
                        if obj is not None:
                            return obj
                    raise NameError(f"Cannot resolve annotation: {annotation}")
            except Exception:
                # Last resort: try to import from common locations
                for module_name in [
                    "typing",
                    "src.domain",
                    "src.application",
                    "src.infrastructure",
                ]:
                    try:
                        module_obj = __import__(module_name, fromlist=[annotation])
                        if hasattr(module_obj, annotation):
                            return getattr(module_obj, annotation)
                    except ImportError:
                        continue

                raise ValueError(f"Could not resolve annotation: {annotation}")

        except Exception as e:
            raise DependencyResolutionError(
                context_class,
                f"Failed to resolve string annotation '{annotation}': {str(e)}",
            )

    def _is_primitive_type(self, param_type: Type) -> bool:
        """Check if a type is a primitive type that can't be resolved as a dependency."""
        primitive_types = {
            str,
            int,
            float,
            bool,
            bytes,
            type(None),
            list,
            dict,
            tuple,
            set,
            frozenset,
        }

        # Check direct primitive types
        if param_type in primitive_types:
            return True

        # Check for typing module types that are essentially primitives
        if hasattr(param_type, "__origin__"):
            origin = param_type.__origin__
            if origin in primitive_types:
                return True

        return False

    def _get_container_instance(self):
        """Get the container instance for factory function parameters."""
        from infrastructure.di.container import get_container

        return get_container()

    def clear_cache(self) -> None:
        """Clear resolution cache."""
        with self._lock:
            self._resolution_cache.clear()
            logger.debug("Dependency resolution cache cleared")

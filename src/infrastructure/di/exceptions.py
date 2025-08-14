"""Dependency injection exceptions."""

from typing import Optional, Type


class DIError(Exception):
    """Base class for dependency injection errors."""


class DependencyResolutionError(DIError):
    """Error raised when a dependency cannot be resolved."""

    def __init__(
        self,
        dependency_type: Type,
        message: str,
        parent_type: Optional[Type] = None,
        parameter_name: Optional[str] = None,
        cause: Optional[Exception] = None,
    ):
        """
        Initialize dependency resolution error.

        Args:
            dependency_type: Type of dependency that could not be resolved
            message: Error message
            parent_type: Optional type that required the dependency
            parameter_name: Optional name of the parameter that could not be resolved
            cause: Optional cause of the error
        """
        self.dependency_type = dependency_type
        self.parent_type = parent_type
        self.parameter_name = parameter_name
        self.cause = cause

        # Build detailed error message
        details = []
        if parent_type:
            parent_name = getattr(parent_type, "__name__", str(parent_type))
            details.append(f"required by {parent_name}")
        if parameter_name:
            details.append(f"parameter '{parameter_name}'")

        detail_str = " ".join(details)
        dependency_name = getattr(dependency_type, "__name__", str(dependency_type))
        if detail_str:
            full_message = (
                f"Failed to resolve dependency {dependency_name} ({detail_str}): {message}"
            )
        else:
            full_message = f"Failed to resolve dependency {dependency_name}: {message}"

        super().__init__(full_message)


class UnregisteredDependencyError(DependencyResolutionError):
    """Error raised when a dependency is not registered with the container."""

    def __init__(
        self,
        dependency_type: Type,
        parent_type: Optional[Type] = None,
        parameter_name: Optional[str] = None,
    ):
        """
        Initialize unregistered dependency error.

        Args:
            dependency_type: Type of dependency that is not registered
            parent_type: Optional type that required the dependency
            parameter_name: Optional name of the parameter that could not be resolved
        """
        message = "Dependency is not registered with the container"
        super().__init__(dependency_type, message, parent_type, parameter_name)


class UntypedParameterError(DependencyResolutionError):
    """Error raised when a parameter has no type annotation."""

    def __init__(self, parent_type: Type, parameter_name: str):
        """
        Initialize untyped parameter error.

        Args:
            parent_type: Type that has the untyped parameter
            parameter_name: Name of the parameter without type annotation
        """
        message = "Parameter has no type annotation"
        # Use str as a placeholder for dependency_type since Any is causing issues
        super().__init__(str, message, parent_type, parameter_name)


class CircularDependencyError(DependencyResolutionError):
    """Error raised when a circular dependency is detected."""

    def __init__(self, dependency_chain: list[Type]):
        """
        Initialize circular dependency error.

        Args:
            dependency_chain: Chain of dependencies that form a circle
        """
        self.dependency_chain = dependency_chain

        # Format the dependency chain for the error message
        chain_str = " -> ".join([getattr(t, "__name__", str(t)) for t in dependency_chain])
        message = f"Circular dependency detected: {chain_str}"

        # Use the last dependency in the chain as the dependency_type
        dependency_type = dependency_chain[-1]
        parent_type = dependency_chain[-2] if len(dependency_chain) > 1 else None

        super().__init__(dependency_type, message, parent_type)


class InstantiationError(DependencyResolutionError):
    """Error raised when a dependency cannot be instantiated."""

    def __init__(
        self,
        dependency_type: Type,
        message: str,
        parent_type: Optional[Type] = None,
        cause: Optional[Exception] = None,
    ):
        """
        Initialize instantiation error.

        Args:
            dependency_type: Type that could not be instantiated
            message: Error message
            parent_type: Optional type that required the dependency
            cause: Optional cause of the error
        """
        super().__init__(dependency_type, message, parent_type, None, cause)


class FactoryError(DependencyResolutionError):
    """Error raised when a factory function fails to create a dependency."""

    def __init__(self, dependency_type: Type, message: str, cause: Optional[Exception] = None):
        """
        Initialize factory error.

        Args:
            dependency_type: Type that the factory failed to create
            message: Error message
            cause: Optional cause of the error
        """
        super().__init__(dependency_type, message, None, None, cause)

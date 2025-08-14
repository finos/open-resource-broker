"""
SDK method discovery from existing CQRS handlers.

Leverages the existing handler discovery system to automatically
expose all registered command and query handlers as SDK methods.
Follows the same patterns as the infrastructure handler discovery.
"""

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type, get_type_hints

from src.application.decorators import (
    get_registered_command_handlers,
    get_registered_query_handlers,
)

from .exceptions import HandlerDiscoveryError, MethodExecutionError


@dataclass
class MethodInfo:
    """Information about a discovered SDK method."""

    name: str
    description: str
    parameters: Dict[str, Any]
    required_params: List[str]
    return_type: Optional[Type]
    handler_type: str  # 'command' or 'query'
    original_class: Type


class SDKMethodDiscovery:
    """
    Discovers and exposes CQRS handlers as SDK methods.

    Follows the same discovery patterns as HandlerDiscoveryService
    but creates SDK method interfaces instead of DI registrations.
    """

    def __init__(self):
        """Initialize the instance."""
        self._method_info_cache: Dict[str, MethodInfo] = {}

    async def discover_cqrs_methods(self, query_bus, command_bus) -> Dict[str, Callable]:
        """
        Auto-discover all CQRS handlers and create SDK methods using direct bus access.

        Args:
            query_bus: Query bus for executing queries
            command_bus: Command bus for executing commands

        Returns:
            Dict mapping method names to callable functions
        """
        methods = {}

        try:
            # Discover query handlers
            query_handlers = get_registered_query_handlers()
            for query_type, handler_class in query_handlers.items():
                method_name = self._query_to_method_name(query_type)
                method_info = self._create_method_info(
                    method_name, query_type, handler_class, "query"
                )
                self._method_info_cache[method_name] = method_info
                methods[method_name] = self._create_query_method_cqrs(
                    query_bus, query_type, method_info
                )

            # Discover command handlers
            command_handlers = get_registered_command_handlers()
            for command_type, handler_class in command_handlers.items():
                method_name = self._command_to_method_name(command_type)
                method_info = self._create_method_info(
                    method_name, command_type, handler_class, "command"
                )
                self._method_info_cache[method_name] = method_info
                methods[method_name] = self._create_command_method_cqrs(
                    command_bus, command_type, method_info
                )

            return methods

        except Exception as e:
            raise HandlerDiscoveryError(f"Failed to discover SDK methods: {str(e)}") from e

    async def discover_sdk_methods(self, service) -> Dict[str, Callable]:
        """
        Legacy method for backward compatibility.

        Args:
            service: Application service instance (deprecated)

        Returns:
            Dict mapping method names to callable functions
        """
        # This method is deprecated but kept for backward compatibility
        # It should not be used in new code
        raise NotImplementedError(
            "discover_sdk_methods is deprecated. Use discover_cqrs_methods instead."
        )

    def get_method_info(self, method_name: str) -> Optional[MethodInfo]:
        """Get information about a specific SDK method."""
        return self._method_info_cache.get(method_name)

    def list_available_methods(self) -> List[str]:
        """List all discovered method names."""
        return list(self._method_info_cache.keys())

    def _query_to_method_name(self, query_type: Type) -> str:
        """
        Convert query class name to SDK method name.

        Examples:
        - ListTemplatesQuery -> list_templates
        - GetRequestStatusQuery -> get_request_status
        """
        name = query_type.__name__
        if name.endswith("Query"):
            name = name[:-5]  # Remove 'Query'
        return self._camel_to_snake(name)

    def _command_to_method_name(self, command_type: Type) -> str:
        """
        Convert command class name to SDK method name.

        Examples:
        - CreateRequestCommand -> create_request
        - UpdateMachineStatusCommand -> update_machine_status
        """
        name = command_type.__name__
        if name.endswith("Command"):
            name = name[:-7]  # Remove 'Command'
        return self._camel_to_snake(name)

    def _camel_to_snake(self, name: str) -> str:
        """Convert CamelCase to snake_case."""
        # Insert underscore before uppercase letters that follow lowercase letters
        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        # Insert underscore before uppercase letters that follow lowercase letters
        # or digits
        return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    def _create_method_info(
        self,
        method_name: str,
        handler_type: Type,
        handler_class: Type,
        operation_type: str,
    ) -> MethodInfo:
        """Create method information from handler type."""
        try:
            # Get type hints for parameters
            type_hints = get_type_hints(handler_type)

            # Extract parameters from the handler type
            parameters = {}
            required_params = []

            if hasattr(handler_type, "__dataclass_fields__"):
                # Pydantic/dataclass model
                for field_name, field in handler_type.__dataclass_fields__.items():
                    parameters[field_name] = {
                        "type": type_hints.get(field_name, "Any"),
                        "required": field.default == field.default_factory(),
                        "description": f"Parameter for {field_name}",
                    }
                    if parameters[field_name]["required"]:
                        required_params.append(field_name)

            # Generate description
            description = self._generate_method_description(method_name, operation_type)

            return MethodInfo(
                name=method_name,
                description=description,
                parameters=parameters,
                required_params=required_params,
                return_type=None,  # Will be determined at runtime
                handler_type=operation_type,
                original_class=handler_type,
            )

        except Exception:
            # Fallback to basic method info
            return MethodInfo(
                name=method_name,
                description=self._generate_method_description(method_name, operation_type),
                parameters={},
                required_params=[],
                return_type=None,
                handler_type=operation_type,
                original_class=handler_type,
            )

    def _generate_method_description(self, method_name: str, operation_type: str) -> str:
        """Generate human-readable description from method name."""
        # Convert snake_case to Title Case
        words = method_name.replace("_", " ").title()
        return f"{words} - {operation_type.title()} operation"

    def _create_query_method_cqrs(
        self, query_bus, query_type: Type, method_info: MethodInfo
    ) -> Callable:
        """Create SDK method for query handler using direct CQRS bus."""

        async def sdk_method(**kwargs):
            try:
                # Create query instance
                query = query_type(**kwargs)

                # Execute via query bus directly
                result = await query_bus.execute(query)

                # Return result (DTOs already have camelCase support)
                return result

            except Exception as e:
                raise MethodExecutionError(
                    f"Failed to execute {method_info.name}: {str(e)}",
                    method_name=method_info.name,
                    details={"query_type": query_type.__name__, "kwargs": kwargs},
                ) from e

        # Add metadata to the method
        sdk_method.__name__ = method_info.name
        sdk_method.__doc__ = method_info.description
        sdk_method._method_info = method_info

        return sdk_method

    def _create_command_method_cqrs(
        self, command_bus, command_type: Type, method_info: MethodInfo
    ) -> Callable:
        """Create SDK method for command handler using direct CQRS bus."""

        async def sdk_method(**kwargs):
            try:
                # Create command instance
                command = command_type(**kwargs)

                # Execute via command bus directly
                result = await command_bus.execute(command)

                # Return result
                return result

            except Exception as e:
                raise MethodExecutionError(
                    f"Failed to execute {method_info.name}: {str(e)}",
                    method_name=method_info.name,
                    details={"command_type": command_type.__name__, "kwargs": kwargs},
                ) from e

        # Add metadata to the method
        sdk_method.__name__ = method_info.name
        sdk_method.__doc__ = method_info.description
        sdk_method._method_info = method_info

        return sdk_method

    # Legacy methods (deprecated)
    def _create_query_method(self, service, query_type: Type, method_info: MethodInfo) -> Callable:
        """Create SDK method for query handler (deprecated)."""
        raise NotImplementedError(
            "Legacy method deprecated. Use _create_query_method_cqrs instead."
        )

    def _create_command_method(
        self, service, command_type: Type, method_info: MethodInfo
    ) -> Callable:
        """Create SDK method for command handler (deprecated)."""
        raise NotImplementedError(
            "Legacy method deprecated. Use _create_command_method_cqrs instead."
        )

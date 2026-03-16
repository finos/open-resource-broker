"""
SDK method discovery from existing CQRS handlers.

Leverages the existing handler discovery system to automatically
expose all registered command and query handlers as SDK methods.
Follows the same patterns as the infrastructure handler discovery.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional, get_type_hints

if TYPE_CHECKING:
    from orb.application.ports.scheduler_port import SchedulerPort

from orb.application.decorators import (
    get_registered_command_handlers,
    get_registered_query_handlers,
)

from .exceptions import HandlerDiscoveryError, MethodExecutionError
from .parameter_mapping import ParameterMapper


@dataclass
class MethodInfo:
    """Information about a discovered SDK method."""

    name: str
    description: str
    parameters: dict[str, Any]
    required_params: list[str]
    return_type: Optional[type]
    handler_type: str  # 'command' or 'query'
    original_class: type


class SDKMethodDiscovery:
    """
    Discovers and exposes CQRS handlers as SDK methods.

    Follows the same discovery patterns as HandlerDiscoveryService
    but creates SDK method interfaces instead of DI registrations.
    """

    # Maps DTO class name -> (scheduler_port_method_name, expects_list)
    # expects_list=True: pass list of original DTO objects to the formatter
    # expects_list=False: pass the serialised dict of a single DTO
    _SCHEDULER_FORMAT_DISPATCH: dict[str, tuple[str, bool]] = {
        "RequestDTO": ("format_request_for_display", False),
        "RequestStatusResponse": ("format_request_status_response", True),
        "ReturnRequestResponse": ("format_request_status_response", True),
        "RequestMachinesResponse": ("format_request_response", False),
        "RequestReturnMachinesResponse": ("format_request_response", False),
        "TemplateDTO": ("format_template_for_display", False),
        "MachineDTO": ("format_machine_details_response", False),
    }

    def __init__(self, scheduler_port: "Optional[SchedulerPort]" = None) -> None:
        """Initialize the instance.

        Args:
            scheduler_port: Optional scheduler port for response formatting.
                When provided, format_* methods are applied after to_dict().
                When None, raw to_dict() output is returned (backwards-compatible).
        """
        self._method_info_cache: dict[str, MethodInfo] = {}
        self._scheduler_port = scheduler_port

    async def discover_cqrs_methods(self, query_bus, command_bus) -> dict[str, Callable]:
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
            raise HandlerDiscoveryError(f"Failed to discover SDK methods: {e!s}")

    async def discover_sdk_methods(self, service) -> dict[str, Callable]:
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

    def list_available_methods(self) -> list[str]:
        """List all discovered method names."""
        return list(self._method_info_cache.keys())

    def _query_to_method_name(self, query_type: type) -> str:
        """
        Convert query class name to SDK method name.

        Examples:
        - ListTemplatesQuery -> list_templates
        - GetRequestQuery -> get_request
        """
        name = query_type.__name__
        if name.endswith("Query"):
            name = name[:-5]  # Remove 'Query'
        return self._camel_to_snake(name)

    def _command_to_method_name(self, command_type: type) -> str:
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

    def _standardize_return_type(self, result: Any) -> Any:
        """
        Standardize return type to dict format for consistent SDK API.

        Args:
            result: Raw result from CQRS handler (DTO, list of DTOs, or primitive)

        Returns:
            Standardized result (dict, list of dicts, or primitive) with JSON-serializable values.
            When a scheduler_port is set, applies the appropriate format_* method as a
            post-processing step based on the DTO class name.
        """
        if result is None:
            return None

        # Single DTO with to_dict method
        if hasattr(result, "to_dict"):
            raw = self._make_json_serializable(result.to_dict())
            return self._apply_scheduler_format(result, raw)

        # List of DTOs
        if isinstance(result, list) and result and hasattr(result[0], "to_dict"):
            items = [self._make_json_serializable(item.to_dict()) for item in result]
            return self._apply_scheduler_format_list(result, items)

        # Already a dict or primitive type
        return self._make_json_serializable(result) if isinstance(result, dict) else result

    def _apply_scheduler_format(self, original: Any, raw: dict) -> Any:
        """Apply scheduler formatting to a single serialised DTO.

        Falls back to raw dict if no scheduler port, no dispatch entry, no method,
        or if the formatter raises.
        """
        if self._scheduler_port is None:
            return raw
        class_name = type(original).__name__
        dispatch = self._SCHEDULER_FORMAT_DISPATCH.get(class_name)
        if dispatch is None:
            return raw
        method_name, expects_list = dispatch
        formatter = getattr(self._scheduler_port, method_name, None)
        if formatter is None:
            return raw
        try:
            if expects_list:
                return formatter([original])
            return formatter(raw)
        except Exception:
            return raw

    def _apply_scheduler_format_list(self, originals: list, raws: list) -> Any:
        """Apply scheduler formatting to a list of serialised DTOs.

        Falls back to raws list if no scheduler port, no dispatch entry, no method,
        or if the formatter raises.
        """
        if self._scheduler_port is None:
            return raws
        if not originals:
            return raws
        class_name = type(originals[0]).__name__
        dispatch = self._SCHEDULER_FORMAT_DISPATCH.get(class_name)
        if dispatch is None:
            return raws
        method_name, expects_list = dispatch
        formatter = getattr(self._scheduler_port, method_name, None)
        if formatter is None:
            return raws
        try:
            if expects_list:
                return formatter(originals)
            return [formatter(r) for r in raws]
        except Exception:
            return raws

    def _make_json_serializable(self, data: dict) -> dict:
        """
        Convert dict values to JSON-serializable format.

        Args:
            data: Dictionary that may contain non-JSON-serializable values

        Returns:
            Dictionary with JSON-serializable values
        """
        from datetime import datetime

        result = {}
        for key, value in data.items():
            if isinstance(value, datetime):
                result[key] = value.isoformat()
            elif isinstance(value, dict):
                result[key] = self._make_json_serializable(value)
            elif isinstance(value, list):
                result[key] = [
                    item.isoformat()
                    if isinstance(item, datetime)
                    else self._make_json_serializable(item)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                result[key] = value
        return result

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
        handler_type: type,
        handler_class: type,
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
                    import dataclasses

                    is_required = (
                        field.default is dataclasses.MISSING
                        and field.default_factory is dataclasses.MISSING  # type: ignore[misc]
                    )
                    parameters[field_name] = {
                        "type": type_hints.get(field_name, "Any"),
                        "required": is_required,
                        "description": f"Parameter for {field_name}",
                    }
                    if is_required:
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
        self, query_bus, query_type: type, method_info: MethodInfo
    ) -> Callable:
        """Create SDK method for query handler using direct CQRS bus."""

        async def sdk_method(**kwargs):
            try:
                # Extract serialization options before CQRS mapping
                raw_response = kwargs.pop("raw_response", False)
                output_format = kwargs.pop("format", None)

                # Map CLI-style parameters to CQRS parameters
                mapped_kwargs = ParameterMapper.map_parameters(query_type, kwargs)

                # Create query instance with mapped parameters
                query = query_type(**mapped_kwargs)

                # Execute via query bus directly
                result = await query_bus.execute(query)

                # Return raw result if requested (skip standardization)
                if raw_response:
                    return result

                # Standardize return type to dict
                standardized = self._standardize_return_type(result)

                # Apply output format if requested
                return self._apply_format(standardized, output_format)

            except MethodExecutionError:
                raise
            except Exception as e:
                raise MethodExecutionError(
                    f"Failed to execute {method_info.name}: {e!s}",
                    method_name=method_info.name,
                    details={
                        "query_type": query_type.__name__,
                        "original_kwargs": kwargs,
                        "mapped_kwargs": ParameterMapper.map_parameters(query_type, kwargs),
                    },
                )

        # Add metadata to the method
        sdk_method.__name__ = method_info.name
        sdk_method.__doc__ = method_info.description
        sdk_method._method_info = method_info

        return sdk_method

    _SUPPORTED_FORMATS = {"json", "yaml"}

    def _apply_format(self, data: Any, output_format: Optional[str]) -> Any:
        """
        Apply output format conversion if requested.

        Args:
            data: Standardized dict/list data
            output_format: 'json', 'yaml', or None (return as-is)

        Returns:
            Formatted string or original data if no format specified
        """
        if not output_format:
            return data

        output_format = output_format.lower()
        if output_format not in self._SUPPORTED_FORMATS:
            from .exceptions import SDKError

            raise SDKError(
                f"Unsupported format: {output_format!r}. "
                f"Supported: {', '.join(sorted(self._SUPPORTED_FORMATS))}"
            )

        if output_format == "json":
            import json

            return json.dumps(data, indent=2, default=str)

        if output_format == "yaml":
            import yaml

            return yaml.dump(data, default_flow_style=False, sort_keys=False)

        return data

    # Output fields populated by handlers on mutable command objects after execution.
    # Keyed by command class name so the lookup is O(1) and requires no imports here.
    _COMMAND_OUTPUT_FIELDS: dict[str, list[str]] = {
        "CreateRequestCommand": ["created_request_id"],
        "CreateReturnRequestCommand": [
            "created_request_ids",
            "processed_machines",
            "skipped_machines",
        ],
        "CleanupOldRequestsCommand": ["requests_cleaned", "request_ids_found"],
        "CleanupAllResourcesCommand": ["requests_cleaned", "machines_cleaned", "total_cleaned"],
        "CreateTemplateCommand": ["created", "validation_errors"],
        "UpdateTemplateCommand": ["updated", "validation_errors"],
        "DeleteTemplateCommand": ["deleted"],
    }

    def _extract_command_output(self, command: Any) -> Any:
        """
        After a command has been executed, check whether the handler populated
        any mutable output fields on the command object and return them as a dict.

        Returns None when no output fields are present so callers that do not
        need a return value are unaffected.
        """
        fields = self._COMMAND_OUTPUT_FIELDS.get(type(command).__name__)
        if not fields:
            return None
        output = {
            f: getattr(command, f, None) for f in fields if getattr(command, f, None) is not None
        }
        return output if output else None

    def _create_command_method_cqrs(
        self, command_bus, command_type: type, method_info: MethodInfo
    ) -> Callable:
        """Create SDK method for command handler using direct CQRS bus."""

        async def sdk_method(**kwargs):
            try:
                # Extract serialization options before CQRS mapping
                raw_response = kwargs.pop("raw_response", False)
                output_format = kwargs.pop("format", None)

                # Map CLI-style parameters to CQRS parameters
                mapped_kwargs = ParameterMapper.map_parameters(command_type, kwargs)

                # Create command instance with mapped parameters
                command = command_type(**mapped_kwargs)

                # Execute via command bus directly
                await command_bus.execute(command)

                # Commands return void; check for handler-populated output fields
                result = self._extract_command_output(command)

                # Return raw result if requested (skip standardization)
                if raw_response:
                    return result

                # Standardize return type to dict
                standardized = self._standardize_return_type(result)

                # Apply output format if requested
                return self._apply_format(standardized, output_format)

            except MethodExecutionError:
                raise
            except Exception as e:
                raise MethodExecutionError(
                    f"Failed to execute {method_info.name}: {e!s}",
                    method_name=method_info.name,
                    details={
                        "command_type": command_type.__name__,
                        "original_kwargs": kwargs,
                        "mapped_kwargs": ParameterMapper.map_parameters(command_type, kwargs),
                    },
                )

        # Add metadata to the method
        sdk_method.__name__ = method_info.name
        sdk_method.__doc__ = method_info.description
        sdk_method._method_info = method_info

        return sdk_method

    # Legacy methods (deprecated)
    def _create_query_method(self, service, query_type: type, method_info: MethodInfo) -> Callable:
        """Create SDK method for query handler (deprecated)."""
        raise NotImplementedError(
            "Legacy method deprecated. Use _create_query_method_cqrs instead."
        )

    def _create_command_method(
        self, service, command_type: type, method_info: MethodInfo
    ) -> Callable:
        """Create SDK method for command handler (deprecated)."""
        raise NotImplementedError(
            "Legacy method deprecated. Use _create_command_method_cqrs instead."
        )

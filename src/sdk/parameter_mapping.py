"""
Parameter mapping layer for SDK to accept CLI-style parameter names.

Maps CLI parameter names to CQRS command/query parameter names while
maintaining backward compatibility.
"""

from typing import Any, Dict, Type


class ParameterMapper:
    """Maps CLI-style parameter names to CQRS parameter names."""

    # Global parameter mappings (apply to all commands/queries)
    GLOBAL_MAPPINGS = {
        "count": "requested_count",  # CLI --count -> CQRS requested_count
        "provider": "provider_name",  # CLI --provider -> CQRS provider_name (if supported)
    }

    # Command-specific mappings
    COMMAND_MAPPINGS = {
        "CreateRequestCommand": {
            "count": "requested_count",
        },
        # Add more command-specific mappings as needed
    }

    # Query-specific mappings
    QUERY_MAPPINGS = {
        # Add query-specific mappings as needed
    }

    @classmethod
    def map_parameters(cls, handler_type: Type, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map CLI-style parameters to CQRS parameters.

        Args:
            handler_type: The CQRS command or query type
            kwargs: Original parameters with CLI-style names

        Returns:
            Mapped parameters with CQRS-style names
        """
        mapped_kwargs = kwargs.copy()
        handler_name = handler_type.__name__

        # Apply global mappings first
        for cli_name, cqrs_name in cls.GLOBAL_MAPPINGS.items():
            if cli_name in mapped_kwargs and cqrs_name not in mapped_kwargs:
                # Only map if the target parameter exists in the handler
                if cls._parameter_exists_in_handler(handler_type, cqrs_name):
                    mapped_kwargs[cqrs_name] = mapped_kwargs.pop(cli_name)

        # Apply command-specific mappings
        if handler_name in cls.COMMAND_MAPPINGS:
            command_mappings = cls.COMMAND_MAPPINGS[handler_name]
            for cli_name, cqrs_name in command_mappings.items():
                if cli_name in mapped_kwargs and cqrs_name not in mapped_kwargs:
                    if cls._parameter_exists_in_handler(handler_type, cqrs_name):
                        mapped_kwargs[cqrs_name] = mapped_kwargs.pop(cli_name)

        # Apply query-specific mappings
        if handler_name in cls.QUERY_MAPPINGS:
            query_mappings = cls.QUERY_MAPPINGS[handler_name]
            for cli_name, cqrs_name in query_mappings.items():
                if cli_name in mapped_kwargs and cqrs_name not in mapped_kwargs:
                    if cls._parameter_exists_in_handler(handler_type, cqrs_name):
                        mapped_kwargs[cqrs_name] = mapped_kwargs.pop(cli_name)

        return mapped_kwargs

    @classmethod
    def _parameter_exists_in_handler(cls, handler_type: Type, param_name: str) -> bool:
        """Check if a parameter exists in the handler type."""
        if hasattr(handler_type, "__dataclass_fields__"):
            return param_name in handler_type.__dataclass_fields__

        # For Pydantic models
        if hasattr(handler_type, "model_fields"):
            return param_name in handler_type.model_fields

        # For regular classes, check __init__ signature
        if hasattr(handler_type, "__init__"):
            import inspect

            sig = inspect.signature(handler_type.__init__)
            return param_name in sig.parameters

        return False

    @classmethod
    def get_supported_parameters(cls, handler_type: Type) -> Dict[str, str]:
        """
        Get all supported parameters for a handler, including mapped names.

        Args:
            handler_type: The CQRS command or query type

        Returns:
            Dict mapping CLI parameter names to CQRS parameter names
        """
        supported = {}
        handler_name = handler_type.__name__

        # Get actual parameters from handler
        actual_params = set()
        if hasattr(handler_type, "__dataclass_fields__"):
            actual_params = set(handler_type.__dataclass_fields__.keys())
        elif hasattr(handler_type, "model_fields"):
            actual_params = set(handler_type.model_fields.keys())

        # Add direct parameters (no mapping needed)
        for param in actual_params:
            supported[param] = param

        # Add global mappings if target parameter exists
        for cli_name, cqrs_name in cls.GLOBAL_MAPPINGS.items():
            if cqrs_name in actual_params:
                supported[cli_name] = cqrs_name

        # Add command-specific mappings
        if handler_name in cls.COMMAND_MAPPINGS:
            for cli_name, cqrs_name in cls.COMMAND_MAPPINGS[handler_name].items():
                if cqrs_name in actual_params:
                    supported[cli_name] = cqrs_name

        # Add query-specific mappings
        if handler_name in cls.QUERY_MAPPINGS:
            for cli_name, cqrs_name in cls.QUERY_MAPPINGS[handler_name].items():
                if cqrs_name in actual_params:
                    supported[cli_name] = cqrs_name

        return supported

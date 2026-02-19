"""
CLI Response Formatter for all response types.

Handles formatting of CQRS Command/Query results, direct function results,
errors, and different output formats with scheduler strategy integration.
"""

from typing import Any, Optional, Union
import traceback

from cli.formatters import format_output
from application.dto.base import BaseResponse, BaseDTO


class CLIResponseFormatter:
    """
    Formats all CLI response types for output.

    Handles:
    - CQRS Command/Query results
    - Direct function results (init, mcp)
    - Error handling with proper formatting
    - Different output formats (JSON, table, etc.)
    - Integration with scheduler strategy formatting
    """

    def __init__(self, scheduler_strategy: Optional[Any] = None):
        """
        Initialize formatter with optional scheduler strategy.

        Args:
            scheduler_strategy: Optional scheduler strategy for specialized formatting
        """
        self.scheduler_strategy = scheduler_strategy

    def format_response(
        self,
        response: Any,
        args: Any = None,
        format_type: Optional[str] = None,
        command_context: Optional[str] = None,
    ) -> Union[str, tuple[str, int]]:
        """
        Format any response type for CLI output.

        Args:
            response: Response to format (CQRS, DTO, dict, string, etc.)
            args: CLI args object (for format extraction)
            format_type: Override format type
            command_context: Optional context for specialized formatting

        Returns:
            Formatted string or tuple of (formatted_string, exit_code)
        """
        try:
            # Determine format type
            output_format = self._get_format_type(args, format_type)

            # Determine command context if not provided
            if not command_context and args:
                command_context = self._extract_command_context(args)

            # Handle different response types
            if self._is_error_response(response):
                return self._format_error_response(response, output_format)

            if self._is_success_with_exit_code(response):
                return self._format_success_with_exit_code(response, output_format)

            # Apply scheduler-specific formatting if available
            if self.scheduler_strategy and command_context:
                # For request_status, pass original response before data extraction
                if command_context == "request_status":
                    formatted_data = self._apply_scheduler_formatting(response, command_context)
                    if formatted_data is not response:
                        return format_output(formatted_data, output_format)
                else:
                    data = self._extract_response_data(response)
                    formatted_data = self._apply_scheduler_formatting(data, command_context)
                    if formatted_data is not data:
                        return format_output(formatted_data, output_format)

            # Extract data from response for default formatting
            if "data" not in locals():
                data = self._extract_response_data(response)

            # Format using existing CLI formatters
            formatted_output = format_output(data, output_format)

            # Return with exit code if response indicates one
            exit_code = self._extract_exit_code(response)
            if exit_code is not None:
                return formatted_output, exit_code

            return formatted_output

        except Exception as e:
            # Fallback error formatting
            return self._format_fallback_error(e, format_type or "json")

    def _get_format_type(self, args: Any, format_override: Optional[str]) -> str:
        """Extract format type from args or use override."""
        if format_override:
            return format_override

        if args and hasattr(args, "format"):
            return args.format

        return "json"  # Default format

    def _extract_command_context(self, args: Any) -> Optional[str]:
        """Extract command context from CLI args."""
        if not args:
            return None

        # Special case: request status should use detailed status formatting
        resource = getattr(args, "resource", None)
        action = getattr(args, "action", None)

        if resource == "requests" and action == "status":
            return "request_status"
        elif resource == "machines" and action == "return":
            return "requests"  # Return requests should be formatted like requests

        # Map resource names to contexts
        resource_context_map = {
            "templates": "templates",
            "template": "templates",
            "requests": "requests",
            "request": "requests",
            "machines": "machines",
            "machine": "machines",
            "providers": "providers",
            "provider": "providers",
        }

        return resource_context_map.get(resource)

    def _is_error_response(self, response: Any) -> bool:
        """Check if response represents an error."""
        if isinstance(response, dict):
            return (
                response.get("error") is True
                or response.get("success") is False
                or "error_message" in response
                or "error_code" in response
            )

        if isinstance(response, BaseResponse):
            return not response.success

        return False

    def _is_success_with_exit_code(self, response: Any) -> bool:
        """Check if response is a success message with exit code."""
        return isinstance(response, dict) and "status" in response and "exit_code" in response

    def _format_error_response(self, response: Any, format_type: str) -> tuple[str, int]:
        """Format error response with appropriate exit code."""
        if isinstance(response, BaseResponse):
            error_data = {
                "error": True,
                "message": response.message or "Operation failed",
                "error_code": response.error_code,
            }
            exit_code = 1
        elif isinstance(response, dict):
            error_data = {
                "error": True,
                "message": (
                    response.get("error_message") or response.get("message") or "Operation failed"
                ),
                "error_code": response.get("error_code"),
            }
            exit_code = response.get("exit_code", 1)
        else:
            error_data = {"error": True, "message": str(response), "error_code": "UNKNOWN_ERROR"}
            exit_code = 1

        formatted_output = format_output(error_data, format_type)
        return formatted_output, exit_code

    def _format_success_with_exit_code(self, response: dict, format_type: str) -> tuple[str, int]:
        """Format success response that includes exit code."""
        # Extract exit code
        exit_code = response.get("exit_code", 0)

        # Remove exit_code from display data
        display_data = {k: v for k, v in response.items() if k != "exit_code"}

        formatted_output = format_output(display_data, format_type)
        return formatted_output, exit_code

    def _extract_response_data(self, response: Any) -> Any:
        """Extract data from various response types."""
        # Handle BaseResponse and subclasses first (before generic to_dict check)
        if isinstance(response, BaseResponse):
            return self._extract_base_response_data(response.to_dict())

        # Handle BaseDTO and subclasses
        if isinstance(response, BaseDTO):
            return response.to_dict()

        # Handle objects with to_dict method
        if hasattr(response, "to_dict") and callable(response.to_dict):
            return response.to_dict()

        # Handle domain objects that might need conversion
        if hasattr(response, "__dict__") and not isinstance(
            response, (str, int, float, bool, list, dict)
        ):
            # Convert domain object to dict
            return self._convert_domain_object_to_dict(response)

        # Return as-is for basic types and dicts
        return response

    def _extract_base_response_data(self, response_dict: dict[str, Any]) -> Any:
        """Extract meaningful data from BaseResponse."""
        # Remove CQRS metadata for CLI display
        data = {
            k: v for k, v in response_dict.items() if k not in ["success", "error_code", "metadata"]
        }

        # If only message remains, return it directly for simple output
        if list(data.keys()) == ["message"] and data["message"]:
            return data["message"]

        # Return cleaned data (remove None values)
        return {k: v for k, v in data.items() if v is not None}

    def _convert_domain_object_to_dict(self, obj: Any) -> dict[str, Any]:
        """Convert domain object to dictionary representation."""
        try:
            # Try common conversion methods
            if hasattr(obj, "model_dump"):
                return obj.model_dump(exclude_none=True)

            if hasattr(obj, "dict"):
                return obj.dict(exclude_none=True)

            # Fallback to __dict__ with filtering
            result = {}
            for key, value in obj.__dict__.items():
                if not key.startswith("_") and value is not None:
                    if hasattr(value, "value"):  # Handle enums
                        result[key] = value.value
                    else:
                        result[key] = value

            return result

        except Exception:
            # Last resort - convert to string
            return {"object": str(obj), "type": obj.__class__.__name__}

    def _extract_exit_code(self, response: Any) -> Optional[int]:
        """Extract exit code from response if present."""
        if isinstance(response, dict):
            return response.get("exit_code")

        if hasattr(response, "exit_code"):
            return response.exit_code

        return None

    def _apply_scheduler_formatting(self, data: Any, context: str) -> Any:
        """Apply scheduler-specific formatting if available."""
        if not self.scheduler_strategy:
            return data

        try:
            # Apply context-specific formatting
            if context == "request_status" and hasattr(
                self.scheduler_strategy, "format_request_status_response"
            ):
                # For request status queries, pass RequestDTO directly to scheduler strategy
                return self.scheduler_strategy.format_request_status_response([data])

            elif context == "templates":
                # For templates list, use format_templates_response for the whole list
                if isinstance(data, list) and hasattr(
                    self.scheduler_strategy, "format_templates_response"
                ):
                    return self.scheduler_strategy.format_templates_response(data)
                elif hasattr(self.scheduler_strategy, "format_template_for_display"):
                    if isinstance(data, dict):
                        if "templates" in data:
                            data["templates"] = [
                                self.scheduler_strategy.format_template_for_display(template)
                                for template in data["templates"]
                            ]
                        elif self._looks_like_template(data):
                            return self.scheduler_strategy.format_template_for_display(data)

            elif context == "requests" and hasattr(
                self.scheduler_strategy, "format_request_response"
            ):
                # For request operations (including machine requests), use format_request_response
                if self._looks_like_single_request(data):
                    return self.scheduler_strategy.format_request_response(data)
                elif isinstance(data, str) and data.startswith("ret-"):
                    # Handle return request ID strings
                    request_data = {
                        "request_id": data,
                        "status": "pending",
                        "message": "Return request created successfully",
                    }
                    return self.scheduler_strategy.format_request_response(request_data)
                elif hasattr(self.scheduler_strategy, "format_request_for_display"):
                    if isinstance(data, list):
                        return [
                            self.scheduler_strategy.format_request_for_display(item)
                            for item in data
                        ]
                    elif isinstance(data, dict):
                        if "requests" in data:
                            data["requests"] = [
                                self.scheduler_strategy.format_request_for_display(request)
                                for request in data["requests"]
                            ]
                        elif self._looks_like_request(data):
                            return self.scheduler_strategy.format_request_for_display(data)

            elif context == "machines":
                # Check if this is a machine show command (single machine details)
                action = getattr(args, "action", None) if args else None
                if action == "show" and hasattr(self.scheduler_strategy, "format_machine_details_response"):
                    if isinstance(data, dict) and self._looks_like_machine(data):
                        return self.scheduler_strategy.format_machine_details_response(data)
                # Check if this is a machine request creation (single request object)
                elif hasattr(
                    self.scheduler_strategy, "format_request_response"
                ) and self._looks_like_single_request(data):
                    return self.scheduler_strategy.format_request_response(data)
                # Handle machine status response (from handle_get_machine_status)
                elif (
                    hasattr(self.scheduler_strategy, "format_machine_status_response")
                    and isinstance(data, dict)
                    and "machines" in data
                ):
                    # Data is already formatted by handle_get_machine_status, return as-is
                    return data
                elif hasattr(self.scheduler_strategy, "format_machine_for_display"):
                    if isinstance(data, list):
                        return [
                            self.scheduler_strategy.format_machine_for_display(item)
                            for item in data
                        ]
                    elif isinstance(data, dict):
                        if "machines" in data:
                            data["machines"] = [
                                self.scheduler_strategy.format_machine_for_display(machine)
                                for machine in data["machines"]
                            ]
                        elif self._looks_like_machine(data):
                            return self.scheduler_strategy.format_machine_for_display(data)

        except Exception:
            # Fallback to original data if formatting fails
            pass

        return data

    def _looks_like_template(self, data: dict) -> bool:
        """Check if data looks like a template object."""
        template_fields = {"template_id", "provider_api", "instance_type", "image_id"}
        return bool(template_fields.intersection(data.keys()))

    def _looks_like_single_request(self, data: dict) -> bool:
        """Check if data looks like a single request object (not a list of requests)."""
        if not isinstance(data, dict):
            return False
        request_fields = {"request_id", "status", "requested_count", "template_id"}
        return bool(request_fields.intersection(data.keys())) and "requests" not in data

    def _looks_like_request(self, data: dict) -> bool:
        """Check if data looks like a request object."""
        request_fields = {"request_id", "status", "requested_count", "template_id"}
        return bool(request_fields.intersection(data.keys()))

    def _looks_like_machine(self, data: dict) -> bool:
        """Check if data looks like a machine object."""
        machine_fields = {"machine_id", "instance_type", "private_ip", "status"}
        return bool(machine_fields.intersection(data.keys()))

    def _format_fallback_error(self, error: Exception, format_type: str) -> tuple[str, int]:
        """Format error when main formatting fails."""
        error_data = {
            "error": True,
            "message": f"Formatting error: {error!s}",
            "type": error.__class__.__name__,
            "traceback": traceback.format_exc() if format_type == "json" else None,
        }

        # Remove None values
        error_data = {k: v for k, v in error_data.items() if v is not None}

        try:
            formatted_output = format_output(error_data, format_type)
        except Exception:
            # Ultimate fallback - plain text
            formatted_output = f"Error: {error!s}"

        return formatted_output, 1

    def format_error(self, error: Exception, format_type: str = "json") -> tuple[str, int]:
        """
        Format error for CLI output.

        Args:
            error: Exception to format
            format_type: Output format

        Returns:
            Tuple of (formatted_string, exit_code)
        """
        error_data = {"error": True, "message": str(error), "type": error.__class__.__name__}

        formatted_output = format_output(error_data, format_type)
        return formatted_output, 1

    def format_success_message(self, message: str, format_type: str = "json") -> str:
        """
        Format success message for CLI output.

        Args:
            message: Success message
            format_type: Output format

        Returns:
            Formatted success string
        """
        if format_type in ["table", "list"]:
            return message

        return format_output({"message": message, "success": True}, format_type)


def create_cli_formatter(scheduler_strategy: Optional[Any] = None) -> CLIResponseFormatter:
    """
    Factory function to create CLI response formatter.

    Args:
        scheduler_strategy: Optional scheduler strategy for specialized formatting

    Returns:
        Configured CLIResponseFormatter instance
    """
    return CLIResponseFormatter(scheduler_strategy)

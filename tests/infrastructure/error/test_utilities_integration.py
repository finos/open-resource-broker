"""Integration tests showing utilities working with ExceptionHandler."""

from domain.base.exceptions import EntityNotFoundError, ValidationError
from infrastructure.error.exception_handler import ExceptionContext, ExceptionHandler
from infrastructure.error.utilities import (
    build_error_context,
    format_error_message,
    format_stack_trace,
    generate_error_code,
)


class TestUtilitiesIntegration:
    """Test utilities integration with existing error handling."""

    def test_utilities_complement_exception_handler(self):
        """Should work alongside ExceptionHandler without conflicts."""
        handler = ExceptionHandler()
        error = ValidationError("Invalid field", error_code="INVALID_FIELD")
        context = ExceptionContext("test_operation")

        # ExceptionHandler preserves domain exception
        handled_error = handler.handle(error, context)
        assert isinstance(handled_error, ValidationError)
        assert handled_error.error_code == "INVALID_FIELD"

        # Utilities provide additional formatting
        formatted_msg = format_error_message(handled_error)
        assert "INVALID_FIELD: Invalid field" in formatted_msg

        error_context = build_error_context(handled_error, operation="test_op")
        assert error_context["error_type"] == "ValidationError"
        assert error_context["operation"] == "test_op"

    def test_utilities_work_with_wrapped_exceptions(self):
        """Should work with exceptions wrapped by ExceptionHandler."""
        handler = ExceptionHandler()
        original_error = ValueError("Invalid value")
        context = ExceptionContext("test_operation")

        # ExceptionHandler wraps ValueError into ValidationError
        wrapped_error = handler.handle(original_error, context)
        assert isinstance(wrapped_error, ValidationError)

        # Utilities work with wrapped exception
        error_code = generate_error_code(wrapped_error)
        assert error_code == "ValidationError"

        formatted_msg = format_error_message(wrapped_error)
        assert "Invalid value" in formatted_msg

    def test_utilities_provide_debugging_info(self):
        """Should provide debugging information not available in ExceptionHandler."""
        error = EntityNotFoundError("User", "123")

        # Stack trace for debugging
        stack_trace = format_stack_trace(error)
        assert "EntityNotFoundError" in stack_trace

        # Rich context for logging
        context = build_error_context(
            error, operation="get_user", user_id="123", request_id="req-456"
        )
        assert context["error_details"]["entity_type"] == "User"
        assert context["error_details"]["entity_id"] == "123"
        assert context["user_id"] == "123"
        assert context["request_id"] == "req-456"

    def test_utilities_handle_edge_cases(self):
        """Should handle edge cases that ExceptionHandler doesn't cover."""
        # Exception with no message
        error = ValueError()
        formatted = format_error_message(error)
        assert "ValueError" in formatted

        # Exception without error_code attribute
        class CustomError(Exception):
            pass

        custom_error = CustomError("test")
        code = generate_error_code(custom_error)
        assert code == "CUSTOM_ERROR"

        context = build_error_context(custom_error, operation="test")
        assert context["error_type"] == "CustomError"

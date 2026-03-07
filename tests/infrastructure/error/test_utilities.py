"""Tests for error handling utilities."""

from orb.domain.base.exceptions import EntityNotFoundError, ValidationError
from orb.infrastructure.error.utilities import (
    build_error_context,
    format_error_message,
    format_stack_trace,
    generate_error_code,
)


class TestFormatErrorMessage:
    """Test error message formatting utility."""

    def test_formats_simple_exception(self):
        """Should format simple exception message."""
        error = ValueError("Invalid input")
        result = format_error_message(error)

        assert "ValueError: Invalid input" in result
        assert "traceback" not in result.lower()

    def test_formats_with_traceback(self):
        """Should include traceback when requested."""
        error = ValueError("Invalid input")
        result = format_error_message(error, include_traceback=True)

        assert "ValueError: Invalid input" in result
        assert "Traceback" in result or "test_formats_with_traceback" in result

    def test_formats_domain_exception(self):
        """Should format domain exception with error code."""
        error = ValidationError("Invalid field", error_code="INVALID_FIELD")
        result = format_error_message(error)

        assert "INVALID_FIELD" in result
        assert "Invalid field" in result

    def test_handles_none_message(self):
        """Should handle exception with None message."""
        error = ValueError(None)
        result = format_error_message(error)

        assert "ValueError" in result


class TestBuildErrorContext:
    """Test error context building utility."""

    def test_builds_basic_context(self):
        """Should build basic error context."""
        error = ValueError("test error")
        context = build_error_context(error, operation="test_op")

        assert context["error_type"] == "ValueError"
        assert context["error_message"] == "test error"
        assert context["operation"] == "test_op"
        assert "timestamp" in context

    def test_includes_additional_kwargs(self):
        """Should include additional context from kwargs."""
        error = ValueError("test")
        context = build_error_context(
            error, operation="test_op", user_id="123", request_id="req-456"
        )

        assert context["user_id"] == "123"
        assert context["request_id"] == "req-456"

    def test_handles_domain_exception_details(self):
        """Should extract details from domain exceptions."""
        error = ValidationError("Invalid field", details={"field": "email", "value": "invalid"})
        context = build_error_context(error, operation="validate")

        assert context["error_details"]["field"] == "email"
        assert context["error_details"]["value"] == "invalid"

    def test_handles_exception_without_details(self):
        """Should handle exceptions without details attribute."""
        error = ValueError("test")
        context = build_error_context(error, operation="test")

        assert "error_details" not in context or context["error_details"] == {}


class TestFormatStackTrace:
    """Test stack trace formatting utility."""

    def test_formats_current_stack(self):
        """Should format current stack trace."""
        result = format_stack_trace()

        assert "test_formats_current_stack" in result
        assert "Current stack trace" in result

    def test_formats_exception_traceback(self):
        """Should format exception traceback."""
        try:
            raise ValueError("test error")
        except ValueError as e:
            result = format_stack_trace(e)

            assert "ValueError: test error" in result
            assert "raise ValueError" in result

    def test_limits_traceback_lines(self):
        """Should limit traceback to specified number of lines."""
        try:
            raise ValueError("test")
        except ValueError as e:
            result = format_stack_trace(e, limit=2)
            lines = result.split("\n")

            # Should have limited number of lines (plus header/footer)
            assert len([line for line in lines if line.strip()]) <= 10

    def test_handles_none_exception(self):
        """Should handle None exception gracefully."""
        result = format_stack_trace(None)

        assert "Current stack trace" in result
        assert "test_handles_none_exception" in result


class TestGenerateErrorCode:
    """Test error code generation utility."""

    def test_generates_from_exception_type(self):
        """Should generate error code from exception type."""
        error = ValueError("test")
        code = generate_error_code(error)

        assert code == "VALUE_ERROR"

    def test_generates_from_domain_exception(self):
        """Should use existing error code from domain exception."""
        error = ValidationError("test", error_code="CUSTOM_CODE")
        code = generate_error_code(error)

        assert code == "CUSTOM_CODE"

    def test_generates_with_prefix(self):
        """Should add prefix when provided."""
        error = ValueError("test")
        code = generate_error_code(error, prefix="API")

        assert code == "API_VALUE_ERROR"

    def test_handles_nested_exception_names(self):
        """Should handle complex exception class names."""
        error = EntityNotFoundError("test", "test-id")
        code = generate_error_code(error)

        assert code == "ENTITY_NOT_FOUND"

    def test_generates_fallback_code(self):
        """Should generate fallback for unknown exceptions."""

        class CustomError(Exception):
            pass

        error = CustomError("test")
        code = generate_error_code(error)

        assert code == "CUSTOM_ERROR"

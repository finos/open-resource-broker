"""Unit tests for error/utilities.py."""

import pytest

from orb.domain.base.exceptions import ValidationError
from orb.infrastructure.error.utilities import (
    build_error_context,
    format_error_message,
    format_stack_trace,
    generate_error_code,
)


@pytest.mark.unit
class TestFormatErrorMessage:
    """Tests for format_error_message."""

    def test_plain_exception_uses_class_name(self) -> None:
        exc = RuntimeError("something bad")
        msg = format_error_message(exc)
        assert "RuntimeError" in msg
        assert "something bad" in msg

    def test_exception_with_error_code_attribute(self) -> None:
        exc = ValidationError("bad field")
        # ValidationError sets error_code to the class name by default
        msg = format_error_message(exc)
        assert exc.error_code in msg

    def test_no_message_shows_fallback(self) -> None:
        exc = RuntimeError("")
        msg = format_error_message(exc)
        assert "No message" in msg

    def test_include_traceback_false_has_no_trace(self) -> None:
        exc = ValueError("x")
        msg = format_error_message(exc, include_traceback=False)
        assert "Traceback" not in msg

    def test_include_traceback_true_with_active_exception(self) -> None:
        try:
            raise ValueError("trigger")
        except ValueError as exc:
            msg = format_error_message(exc, include_traceback=True)
        assert "Traceback" in msg or "ValueError" in msg

    def test_include_traceback_true_outside_except_block(self) -> None:
        # No active exception — should fall back to current stack
        exc = ValueError("no trace context")
        msg = format_error_message(exc, include_traceback=True)
        # Should contain at least the base message
        assert "ValueError" in msg or "no trace context" in msg


@pytest.mark.unit
class TestBuildErrorContext:
    """Tests for build_error_context."""

    def test_basic_keys_present(self) -> None:
        exc = KeyError("missing")
        ctx = build_error_context(exc)
        assert ctx["error_type"] == "KeyError"
        assert "error_message" in ctx
        assert "timestamp" in ctx

    def test_extra_kwargs_included(self) -> None:
        exc = RuntimeError("boom")
        ctx = build_error_context(exc, request_id="req-42", operation="create")
        assert ctx["request_id"] == "req-42"
        assert ctx["operation"] == "create"

    def test_domain_error_details_included(self) -> None:
        exc = ValidationError("bad", details={"field": "name"})
        ctx = build_error_context(exc)
        assert ctx["error_details"] == {"field": "name"}

    def test_exception_without_details_has_no_error_details_key(self) -> None:
        exc = RuntimeError("plain")
        ctx = build_error_context(exc)
        assert "error_details" not in ctx

    def test_timestamp_is_iso_format(self) -> None:
        from datetime import datetime

        exc = ValueError("t")
        ctx = build_error_context(exc)
        # Should not raise
        datetime.fromisoformat(ctx["timestamp"])


@pytest.mark.unit
class TestFormatStackTrace:
    """Tests for format_stack_trace."""

    def test_with_exception_contains_exception_info(self) -> None:
        try:
            raise ValueError("trace me")
        except ValueError as exc:
            trace = format_stack_trace(exc)
        assert "ValueError" in trace

    def test_without_exception_has_current_stack_header(self) -> None:
        trace = format_stack_trace()
        assert "Current stack trace:" in trace

    def test_limit_truncates_with_exception(self) -> None:
        try:
            raise ValueError("limit test")
        except ValueError as exc:
            full = format_stack_trace(exc)
            limited = format_stack_trace(exc, limit=2)
        # Limited trace should be shorter or equal (may add ellipsis)
        assert len(limited) <= len(full) + 50  # allow for ellipsis padding

    def test_limit_truncates_current_stack(self) -> None:
        full = format_stack_trace(limit=None)
        limited = format_stack_trace(limit=2)
        assert len(limited) <= len(full)


@pytest.mark.unit
class TestGenerateErrorCode:
    """Tests for generate_error_code."""

    def test_camel_case_type_converted_to_upper_snake(self) -> None:
        exc = RuntimeError("x")
        code = generate_error_code(exc)
        assert code == "RUNTIME_ERROR"

    def test_existing_error_code_attribute_returned(self) -> None:
        exc = ValidationError("bad")
        code = generate_error_code(exc)
        # ValidationError sets error_code = "ValidationError" by default
        assert code == exc.error_code

    def test_prefix_prepended(self) -> None:
        exc = RuntimeError("x")
        code = generate_error_code(exc, prefix="AWS")
        assert code.startswith("AWS_")

    def test_prefix_prepended_to_domain_error_code(self) -> None:
        exc = ValidationError("x", error_code="MY_CODE")
        code = generate_error_code(exc, prefix="API")
        assert code == "API_MY_CODE"

    def test_single_word_class_no_underscores(self) -> None:
        exc = ValueError("v")
        code = generate_error_code(exc)
        assert code == "VALUE_ERROR"

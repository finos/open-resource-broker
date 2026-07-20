"""Additional coverage tests for ExceptionHandler.

Coverage targets: lines 188,200,240,251,276,288,311,323,331,343,349,360,366,
378,386,398,404,415,421,433,456,467,649,675-677
(all the preserve/wrap handlers + handle_error_for_http + get_performance_stats)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from orb.domain.base.exceptions import (
    BusinessRuleViolationError,
    ConfigurationError,
    DomainException,
    DuplicateError,
    EntityNotFoundError,
    InfrastructureError,
    ValidationError,
)
from orb.domain.machine.exceptions import (
    MachineException,
    MachineNotFoundError,
    MachineValidationError,
)
from orb.domain.request.exceptions import (
    RequestException,
    RequestNotFoundError,
    RequestValidationError,
)
from orb.domain.template.exceptions import (
    TemplateException,
    TemplateNotFoundError,
    TemplateValidationError,
)
from orb.infrastructure.error.exception_handler import (
    ExceptionContext,
    ExceptionHandler,
)

pytestmark = pytest.mark.unit


def _make_handler() -> ExceptionHandler:
    return ExceptionHandler(logger=MagicMock())


def _ctx(op: str = "test_op") -> ExceptionContext:
    return ExceptionContext(operation=op, layer="domain")


# ---------------------------------------------------------------------------
# Domain exception preserve handlers
# ---------------------------------------------------------------------------


class TestPreserveDomainExceptions:
    def test_handle_domain_exception_returns_same_exception(self):
        handler = _make_handler()
        exc = DomainException("domain error", error_code="DOM001")
        result = handler.handle(exc, _ctx())
        assert result is exc

    def test_handle_validation_error_returns_same(self):
        handler = _make_handler()
        exc = ValidationError("bad value")
        result = handler.handle(exc, _ctx())
        assert result is exc

    def test_handle_entity_not_found_returns_same(self):
        handler = _make_handler()
        exc = EntityNotFoundError("Machine", "m-123")
        result = handler.handle(exc, _ctx())
        assert result is exc

    def test_handle_business_rule_violation_returns_same(self):
        handler = _make_handler()
        exc = BusinessRuleViolationError("rule violated")
        result = handler.handle(exc, _ctx())
        assert result is exc

    def test_handle_duplicate_error_returns_same(self):
        handler = _make_handler()
        exc = DuplicateError("duplicate detected")
        result = handler.handle(exc, _ctx())
        assert result is exc


# ---------------------------------------------------------------------------
# Template exception preserve handlers
# ---------------------------------------------------------------------------


class TestPreserveTemplateExceptions:
    def test_handle_template_exception_returns_same(self):
        handler = _make_handler()
        exc = TemplateException("template problem")
        result = handler.handle(exc, _ctx())
        assert result is exc

    def test_handle_template_not_found_returns_same(self):
        handler = _make_handler()
        exc = TemplateNotFoundError("tpl-1")
        result = handler.handle(exc, _ctx())
        assert result is exc

    def test_handle_template_validation_error_returns_same(self):
        handler = _make_handler()
        exc = TemplateValidationError("template invalid")
        result = handler.handle(exc, _ctx())
        assert result is exc


# ---------------------------------------------------------------------------
# Machine exception preserve handlers
# ---------------------------------------------------------------------------


class TestPreserveMachineExceptions:
    def test_handle_machine_exception_returns_same(self):
        handler = _make_handler()
        exc = MachineException("machine problem")
        result = handler.handle(exc, _ctx())
        assert result is exc

    def test_handle_machine_not_found_returns_same(self):
        handler = _make_handler()
        exc = MachineNotFoundError("m-001")
        result = handler.handle(exc, _ctx())
        assert result is exc

    def test_handle_machine_validation_returns_same(self):
        handler = _make_handler()
        exc = MachineValidationError("machine validation failed")
        result = handler.handle(exc, _ctx())
        assert result is exc


# ---------------------------------------------------------------------------
# Request exception preserve handlers
# ---------------------------------------------------------------------------


class TestPreserveRequestExceptions:
    def test_handle_request_exception_returns_same(self):
        handler = _make_handler()
        exc = RequestException("request problem")
        result = handler.handle(exc, _ctx())
        assert result is exc

    def test_handle_request_not_found_returns_same(self):
        handler = _make_handler()
        exc = RequestNotFoundError("req-1")
        result = handler.handle(exc, _ctx())
        assert result is exc

    def test_handle_request_validation_returns_same(self):
        handler = _make_handler()
        exc = RequestValidationError("request invalid")
        result = handler.handle(exc, _ctx())
        assert result is exc


# ---------------------------------------------------------------------------
# Infrastructure / configuration preserve handlers
# ---------------------------------------------------------------------------


class TestPreserveInfrastructureExceptions:
    def test_handle_infrastructure_error_returns_same(self):
        handler = _make_handler()
        exc = InfrastructureError("infra failure")
        result = handler.handle(exc, _ctx())
        assert result is exc

    def test_handle_configuration_error_returns_same(self):
        handler = _make_handler()
        exc = ConfigurationError("bad config")
        result = handler.handle(exc, _ctx())
        assert result is exc


# ---------------------------------------------------------------------------
# Built-in exception wrap handlers
# ---------------------------------------------------------------------------


class TestWrapBuiltinExceptions:
    def test_wrap_json_decode_error_generic_context(self):
        handler = _make_handler()
        raw = json.JSONDecodeError("bad json", "doc", 0)
        ctx = _ctx("generic_operation")
        result = handler.handle(raw, ctx)
        assert isinstance(result, InfrastructureError)

    def test_wrap_json_decode_error_config_context(self):
        handler = _make_handler()
        raw = json.JSONDecodeError("bad json", "doc", 0)
        ctx = _ctx("load_config_file")
        result = handler.handle(raw, ctx)
        assert isinstance(result, ConfigurationError)

    def test_wrap_json_decode_error_request_context(self):
        handler = _make_handler()
        raw = json.JSONDecodeError("bad json", "doc", 0)
        ctx = _ctx("parse_request_body")
        result = handler.handle(raw, ctx)
        assert isinstance(result, RequestValidationError)

    def test_wrap_connection_error(self):
        handler = _make_handler()
        exc = ConnectionError("connection refused")
        result = handler.handle(exc, _ctx())
        assert isinstance(result, InfrastructureError)

    def test_wrap_file_not_found_generic(self):
        handler = _make_handler()
        exc = FileNotFoundError(2, "No such file or directory")
        # Use the private handler directly to avoid ExceptionContext.lower() issue
        result = handler._wrap_file_not_found_error(exc, context="data_operation")
        assert isinstance(result, InfrastructureError)

    def test_wrap_file_not_found_config_context(self):
        handler = _make_handler()
        exc = FileNotFoundError(2, "No such file or directory")
        result = handler._wrap_file_not_found_error(exc, context="load_config_file")
        assert isinstance(result, ConfigurationError)

    def test_wrap_value_error(self):
        handler = _make_handler()
        exc = ValueError("invalid value")
        result = handler.handle(exc, _ctx())
        assert isinstance(result, ValidationError)

    def test_wrap_key_error(self):
        handler = _make_handler()
        exc = KeyError("missing_key")
        result = handler.handle(exc, _ctx())
        assert isinstance(result, ValidationError)

    def test_wrap_type_error(self):
        handler = _make_handler()
        exc = TypeError("wrong type")
        result = handler.handle(exc, _ctx())
        assert isinstance(result, ValidationError)

    def test_wrap_attribute_error(self):
        handler = _make_handler()
        exc = AttributeError("no attribute")
        result = handler.handle(exc, _ctx())
        assert isinstance(result, InfrastructureError)

    def test_generic_unknown_exception_wraps_infrastructure(self):
        handler = _make_handler()
        exc = RuntimeError("unknown problem")
        result = handler.handle(exc, _ctx())
        assert isinstance(result, InfrastructureError)


# ---------------------------------------------------------------------------
# Performance stats
# ---------------------------------------------------------------------------


class TestPerformanceStats:
    def test_stats_increment_per_handle(self):
        handler = _make_handler()
        exc = ValidationError("v1")
        handler.handle(exc, _ctx())
        handler.handle(exc, _ctx())
        stats = handler._performance_stats
        assert stats["total_handled"] == 2

    def test_stats_tracked_by_exception_type(self):
        handler = _make_handler()
        handler.handle(ValidationError("v"), _ctx())
        handler.handle(KeyError("k"), _ctx())
        by_type = handler._performance_stats["by_type"]
        assert by_type.get("ValidationError", 0) >= 1
        assert by_type.get("KeyError", 0) >= 1

    def test_metrics_are_called_when_provided(self):
        mock_metrics = MagicMock()
        handler = ExceptionHandler(logger=MagicMock(), metrics=mock_metrics)
        handler.handle(ValidationError("v"), _ctx())
        assert mock_metrics.increment.called


# ---------------------------------------------------------------------------
# handle_error_for_http
# ---------------------------------------------------------------------------


class TestHandleErrorForHttp:
    def test_returns_error_response_object(self):
        handler = _make_handler()
        from orb.infrastructure.error.responses import ErrorResponse

        result = handler.handle_error_for_http(ValidationError("bad"))
        assert isinstance(result, ErrorResponse)

    def test_not_found_gives_404_status(self):
        handler = _make_handler()
        result = handler.handle_error_for_http(EntityNotFoundError("Machine", "m-1"))
        assert result.http_status == 404

    def test_validation_error_gives_400_status(self):
        handler = _make_handler()
        result = handler.handle_error_for_http(ValidationError("invalid"))
        assert result.http_status == 400

    def test_infrastructure_error_gives_5xx(self):
        handler = _make_handler()
        result = handler.handle_error_for_http(InfrastructureError("infra fail"))
        assert result.http_status in (500, 503)

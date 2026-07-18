"""Unit tests for uncovered branches in orb.infrastructure.logging.logger."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.logging import logger as logger_module
from orb.infrastructure.logging.logger import (
    AuditLogger,
    ColoredFormatter,
    ContextLogger,
    JsonFormatter,
    LoggerAdapter,
    MetricsLogger,
    RequestLogger,
    get_logger,
    setup_audit_logger,
    with_context,
)


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """Reset logging initialized flag and handlers around each test."""
    original_initialized = logger_module._logging_initialized
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    for h in list(root.handlers):
        root.removeHandler(h)
    logger_module._logging_initialized = False
    yield
    logger_module._logging_initialized = False
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in original_handlers:
        root.addHandler(h)
    logger_module._logging_initialized = original_initialized


# ---------------------------------------------------------------------------
# ColoredFormatter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestColoredFormatter:
    def test_format_adds_color_codes(self) -> None:
        formatter = ColoredFormatter("%(levelname)s %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="/a/b/c/d/e/f.py",
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        # Color codes should be in the result
        assert "\033[" in result

    def test_format_shortens_long_pathname(self) -> None:
        formatter = ColoredFormatter("%(pathname)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/a/b/c/d/e/f.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        # Pathname should be shortened (parts after first 5 folders)
        assert "/a/b/c/d/e/f.py" not in result

    def test_format_handles_short_pathname(self) -> None:
        formatter = ColoredFormatter("%(pathname)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/short/path.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        # Should not crash for short paths
        formatter.format(record)


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestJsonFormatter:
    def test_format_produces_valid_json_output(self) -> None:
        import json

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/src/orb/something.py",
            lineno=42,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        parsed = json.loads(result)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"

    def test_format_includes_extra_fields_from_constructor(self) -> None:
        import json

        formatter = JsonFormatter(env="prod", service="orb")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        parsed = json.loads(formatter.format(record))
        assert parsed.get("env") == "prod"
        assert parsed.get("service") == "orb"

    def test_format_includes_exception_info(self) -> None:
        import json

        formatter = JsonFormatter()
        try:
            raise ValueError("test exc")
        except ValueError:
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="/path.py",
            lineno=1,
            msg="err",
            args=(),
            exc_info=exc_info,
        )
        parsed = json.loads(formatter.format(record))
        assert "exception" in parsed

    def test_format_includes_request_id_when_present(self) -> None:
        import json

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        record.request_id = "req-123"  # type: ignore[attr-defined]
        parsed = json.loads(formatter.format(record))
        assert parsed.get("request_id") == "req-123"

    def test_format_includes_correlation_id_when_present(self) -> None:
        import json

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        record.correlation_id = "corr-456"  # type: ignore[attr-defined]
        parsed = json.loads(formatter.format(record))
        assert parsed.get("correlation_id") == "corr-456"

    def test_format_includes_extra_dict_when_present(self) -> None:
        import json

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        record.extra = {"custom_key": "custom_value"}  # type: ignore[attr-defined]
        parsed = json.loads(formatter.format(record))
        assert parsed.get("custom_key") == "custom_value"

    def test_format_path_without_src_uses_full_path(self) -> None:
        import json

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/no/src/here.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        parsed = json.loads(formatter.format(record))
        assert "file" in parsed


# ---------------------------------------------------------------------------
# ContextLogger
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContextLogger:
    def test_bind_adds_context(self) -> None:
        ctx_logger = ContextLogger("test_bind_ctx")
        ctx_logger.bind(req="abc")
        assert ctx_logger._context.get("req") == "abc"

    def test_unbind_removes_context(self) -> None:
        ctx_logger = ContextLogger("test_unbind_ctx")
        ctx_logger.bind(req="abc")
        ctx_logger.unbind("req")
        assert "req" not in ctx_logger._context

    def test_unbind_nonexistent_key_is_noop(self) -> None:
        ctx_logger = ContextLogger("test_unbind_noop_ctx")
        ctx_logger.unbind("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# setup_logging — file handler path
# ---------------------------------------------------------------------------


_LOG_FMT = "%(levelname)s %(message)s"


@pytest.mark.unit
class TestSetupLogging:
    def test_second_call_is_idempotent(self) -> None:
        from orb.config.schemas.logging_schema import LoggingConfig

        config = LoggingConfig(
            level="INFO",
            format=_LOG_FMT,
            file_path=None,
            console_enabled=False,
            max_size=10485760,
            backup_count=5,
        )
        logger_module.setup_logging(config)
        logger_module.setup_logging(config)  # second call must be a noop
        assert logger_module._logging_initialized is True

    def test_file_handler_added_when_file_path_set(self, tmp_path) -> None:
        from orb.config.schemas.logging_schema import LoggingConfig

        log_file = str(tmp_path / "orb.log")
        config = LoggingConfig(
            level="INFO",
            format=_LOG_FMT,
            file_path=log_file,
            console_enabled=False,
            max_size=10485760,
            backup_count=5,
        )
        logger_module.setup_logging(config)
        root = logging.getLogger()
        file_handlers = [
            h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) > 0

    def test_console_handler_not_added_when_disabled(self) -> None:
        from orb.config.schemas.logging_schema import LoggingConfig

        config = LoggingConfig(
            level="INFO",
            format=_LOG_FMT,
            file_path=None,
            console_enabled=False,
            max_size=10485760,
            backup_count=5,
        )
        logger_module.setup_logging(config)
        root = logging.getLogger()
        stream_handlers = [h for h in root.handlers if type(h) is logging.StreamHandler]
        assert len(stream_handlers) == 0


# ---------------------------------------------------------------------------
# setup_audit_logger
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetupAuditLogger:
    def test_adds_stream_handler_when_no_file(self) -> None:
        audit = logging.getLogger("orb.audit")
        # Clean up handlers first
        for h in list(audit.handlers):
            audit.removeHandler(h)
        setup_audit_logger(audit_log_file=None)
        assert len(audit.handlers) > 0
        assert isinstance(audit.handlers[0], logging.StreamHandler)

    def test_idempotent_on_second_call_without_file(self) -> None:
        audit = logging.getLogger("orb.audit")
        for h in list(audit.handlers):
            audit.removeHandler(h)
        setup_audit_logger(audit_log_file=None)
        count_before = len(audit.handlers)
        # Second call should NOT add another handler (already has one)
        setup_audit_logger(audit_log_file=None)
        assert len(audit.handlers) == count_before

    def test_adds_file_handler_when_file_path_set(self, tmp_path) -> None:
        audit = logging.getLogger("orb.audit")
        for h in list(audit.handlers):
            audit.removeHandler(h)
        log_file = str(tmp_path / "audit.log")
        setup_audit_logger(audit_log_file=log_file)
        file_handlers = [
            h for h in audit.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) > 0

    def test_idempotent_for_same_file_path(self, tmp_path) -> None:
        audit = logging.getLogger("orb.audit")
        for h in list(audit.handlers):
            audit.removeHandler(h)
        log_file = str(tmp_path / "audit2.log")
        setup_audit_logger(audit_log_file=log_file)
        count_before = len(audit.handlers)
        setup_audit_logger(audit_log_file=log_file)
        assert len(audit.handlers) == count_before


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditLogger:
    def test_log_event_calls_logger_info(self) -> None:
        al = AuditLogger()
        al.logger = MagicMock()
        al.log_event(
            event_type="ACCESS",
            user="alice",
            action="read",
            resource="machine-1",
            status="success",
            details={"ip": "10.0.0.1"},
        )
        al.logger.info.assert_called_once()

    def test_log_event_without_details(self) -> None:
        al = AuditLogger()
        al.logger = MagicMock()
        al.log_event(
            event_type="MODIFY",
            user="bob",
            action="write",
            resource="template-1",
            status="success",
        )
        al.logger.info.assert_called_once()


# ---------------------------------------------------------------------------
# MetricsLogger
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetricsLogger:
    def test_log_timing_calls_info(self) -> None:
        ml = MetricsLogger()
        ml.logger = MagicMock()
        ml.log_timing("request_latency", 42.5, status="success", region="us-east-1")
        ml.logger.info.assert_called_once()
        call_kwargs = ml.logger.info.call_args
        extra = call_kwargs[1].get("extra", {}) if call_kwargs[1] else {}
        assert extra.get("metric_type") == "timing"
        assert extra.get("duration_ms") == 42.5

    def test_log_counter_calls_info(self) -> None:
        ml = MetricsLogger()
        ml.logger = MagicMock()
        ml.log_counter("machine_requests", value=5)
        ml.logger.info.assert_called_once()
        extra = ml.logger.info.call_args[1].get("extra", {})
        assert extra.get("metric_type") == "counter"
        assert extra.get("value") == 5

    def test_log_gauge_calls_info(self) -> None:
        ml = MetricsLogger()
        ml.logger = MagicMock()
        ml.log_gauge("active_machines", 12.0)
        ml.logger.info.assert_called_once()
        extra = ml.logger.info.call_args[1].get("extra", {})
        assert extra.get("metric_type") == "gauge"
        assert extra.get("value") == 12.0


# ---------------------------------------------------------------------------
# LoggerAdapter & with_context
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoggerAdapterAndWithContext:
    def test_with_context_returns_adapter(self) -> None:
        adapter = with_context(request_id="req-1")
        assert isinstance(adapter, LoggerAdapter)

    def test_adapter_process_merges_extra(self) -> None:
        base_logger = get_logger("test_adapter_logger")
        adapter = LoggerAdapter(base_logger, {"ctx": "value"})
        _msg, kwargs = adapter.process("hello", {})
        assert kwargs["extra"]["ctx"] == "value"

    def test_adapter_process_merges_existing_extra(self) -> None:
        base_logger = get_logger("test_adapter_logger_extra")
        adapter = LoggerAdapter(base_logger, {"ctx": "value"})
        _msg, kwargs = adapter.process("hello", {"extra": {"x": 1}})
        assert kwargs["extra"]["ctx"] == "value"
        assert kwargs["extra"]["x"] == 1


# ---------------------------------------------------------------------------
# RequestLogger
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestLogger:
    def test_info_delegates(self) -> None:
        rl = RequestLogger("req-1")
        rl.logger = MagicMock()
        rl.info("hello")
        rl.logger.info.assert_called()

    def test_error_delegates(self) -> None:
        rl = RequestLogger("req-1")
        rl.logger = MagicMock()
        rl.error("error msg")
        rl.logger.error.assert_called()

    def test_warning_delegates(self) -> None:
        rl = RequestLogger("req-1")
        rl.logger = MagicMock()
        rl.warning("warn msg")
        rl.logger.warning.assert_called()

    def test_debug_delegates(self) -> None:
        rl = RequestLogger("req-1")
        rl.logger = MagicMock()
        rl.debug("debug msg")
        rl.logger.debug.assert_called()

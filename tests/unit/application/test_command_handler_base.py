"""Unit tests for application/base/command_handler.py.

Covers ApplicationCommandHandler and CLICommandHandler branches.
"""

from __future__ import annotations

import json
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.application.base.command_handler import ApplicationCommandHandler, CLICommandHandler

# ---------------------------------------------------------------------------
# Concrete subclasses for testing
# ---------------------------------------------------------------------------


class _ConcreteAppHandler(ApplicationCommandHandler):
    """Minimal concrete subclass so we can instantiate the abstract base."""

    async def handle(self, command: Any) -> None:  # type: ignore[override]
        pass


class _ConcreteCLIHandler(CLICommandHandler):
    """Minimal concrete subclass."""

    async def handle(self, command: Any) -> None:  # type: ignore[override]
        pass


# ---------------------------------------------------------------------------
# ApplicationCommandHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplicationCommandHandler:
    def _make_handler(self, **kwargs) -> _ConcreteAppHandler:
        return _ConcreteAppHandler(**kwargs)

    def test_init_defaults_none(self):
        h = self._make_handler()
        assert h.logger is None
        assert h.metrics is None
        assert h.event_publisher is None

    def test_init_stores_dependencies(self):
        logger = MagicMock()
        metrics = MagicMock()
        pub = MagicMock()
        h = self._make_handler(logger=logger, metrics=metrics, event_publisher=pub)
        assert h.logger is logger
        assert h.metrics is metrics
        assert h.event_publisher is pub

    def test_publish_event_calls_publisher(self):
        pub = MagicMock()
        h = self._make_handler(event_publisher=pub)
        h._publish_event("some-event")
        pub.publish.assert_called_once_with("some-event")

    def test_publish_event_no_op_when_publisher_none(self):
        h = self._make_handler()
        # Must not raise
        h._publish_event("evt")

    def test_log_info_calls_logger(self):
        logger = MagicMock()
        h = self._make_handler(logger=logger)
        h._log_info("hello %s", extra="x")
        logger.info.assert_called_once_with("hello %s", extra="x")

    def test_log_info_no_op_when_logger_none(self):
        h = self._make_handler()
        h._log_info("noop")  # must not raise

    def test_log_error_calls_logger(self):
        logger = MagicMock()
        h = self._make_handler(logger=logger)
        h._log_error("err %s", extra="e")
        logger.error.assert_called_once_with("err %s", extra="e")

    def test_log_error_no_op_when_logger_none(self):
        h = self._make_handler()
        h._log_error("noop")  # must not raise

    def test_record_metric_calls_metrics(self):
        metrics = MagicMock()
        h = self._make_handler(metrics=metrics)
        h._record_metric("latency", 42, region="us-east-1")
        metrics.record.assert_called_once_with("latency", 42, region="us-east-1")

    def test_record_metric_no_op_when_metrics_none(self):
        h = self._make_handler()
        h._record_metric("m", 1)  # must not raise


# ---------------------------------------------------------------------------
# CLICommandHandler — init validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCLICommandHandlerInit:
    def test_raises_when_query_bus_missing(self):
        with pytest.raises(ValueError, match="QueryBus"):
            _ConcreteCLIHandler(query_bus=None, command_bus=MagicMock())

    def test_raises_when_command_bus_missing(self):
        with pytest.raises(ValueError, match="CommandBus"):
            _ConcreteCLIHandler(query_bus=MagicMock(), command_bus=None)

    def test_init_success_stores_buses(self):
        qb = MagicMock()
        cb = MagicMock()
        h = _ConcreteCLIHandler(query_bus=qb, command_bus=cb)
        assert h._query_bus is qb
        assert h._command_bus is cb

    def test_init_accepts_optional_logger_and_metrics(self):
        logger = MagicMock()
        metrics = MagicMock()
        h = _ConcreteCLIHandler(
            query_bus=MagicMock(), command_bus=MagicMock(), logger=logger, metrics=metrics
        )
        assert h.logger is logger
        assert h.metrics is metrics


# ---------------------------------------------------------------------------
# CLICommandHandler.process_input
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCLICommandHandlerProcessInput:
    def _handler(self):
        return _ConcreteCLIHandler(query_bus=MagicMock(), command_bus=MagicMock())

    def test_returns_none_when_no_file_or_data(self):
        h = self._handler()
        cmd = MagicMock(spec=[])  # no 'file' or 'data' attr
        assert h.process_input(cmd) is None

    def test_loads_json_from_file(self):
        h = self._handler()
        payload = {"key": "value", "n": 42}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(payload, tmp)
            tmp_path = tmp.name

        cmd = MagicMock()
        cmd.file = tmp_path
        cmd.data = None
        result = h.process_input(cmd)
        assert result == payload

    def test_file_not_found_raises_value_error(self):
        h = self._handler()
        cmd = MagicMock()
        cmd.file = "/nonexistent/path/file.json"
        cmd.data = None
        with pytest.raises(ValueError, match="Input file not found"):
            h.process_input(cmd)

    def test_invalid_json_in_file_raises_value_error(self):
        h = self._handler()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            tmp.write("{not valid json}")
            tmp_path = tmp.name

        cmd = MagicMock()
        cmd.file = tmp_path
        cmd.data = None
        with pytest.raises(ValueError, match="Invalid JSON in file"):
            h.process_input(cmd)

    def test_loads_json_from_data_string(self):
        h = self._handler()
        cmd = MagicMock(spec=["data"])
        cmd.data = '{"x": 1}'
        result = h.process_input(cmd)
        assert result == {"x": 1}

    def test_invalid_json_data_string_raises_value_error(self):
        h = self._handler()
        cmd = MagicMock(spec=["data"])
        cmd.data = "not-json"
        with pytest.raises(ValueError, match="Invalid JSON data"):
            h.process_input(cmd)

    def test_file_error_logs_and_raises_value_error_not_found(self):
        logger = MagicMock()
        h = _ConcreteCLIHandler(query_bus=MagicMock(), command_bus=MagicMock(), logger=logger)
        cmd = MagicMock()
        cmd.file = "/does/not/exist.json"
        cmd.data = None
        with pytest.raises(ValueError):
            h.process_input(cmd)
        logger.error.assert_called()

    def test_file_exception_propagates(self):
        """An unexpected error reading the file re-raises the original exception."""
        logger = MagicMock()
        h = _ConcreteCLIHandler(query_bus=MagicMock(), command_bus=MagicMock(), logger=logger)

        cmd = MagicMock()
        cmd.file = "/some/protected/file.json"
        cmd.data = None

        # A PermissionError is neither FileNotFoundError nor JSONDecodeError, so it
        # hits the bare `except Exception: raise` branch and must propagate unchanged.
        with patch("builtins.open", side_effect=PermissionError("denied")):
            with pytest.raises(PermissionError, match="denied"):
                h.process_input(cmd)

        # The original exception is logged before being re-raised.
        logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# CLICommandHandler.format_output
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCLICommandHandlerFormatOutput:
    def _handler(self):
        return _ConcreteCLIHandler(query_bus=MagicMock(), command_bus=MagicMock())

    def test_formats_object_with_dict_as_json(self):
        h = self._handler()

        class _Obj:
            def __init__(self):
                self.foo = "bar"
                self.n = 7

        out = h.format_output(_Obj())
        parsed = json.loads(out)
        assert parsed["foo"] == "bar"
        assert parsed["n"] == 7

    def test_formats_plain_string(self):
        h = self._handler()
        out = h.format_output("hello")
        assert out == "hello"

    def test_formats_integer(self):
        h = self._handler()
        assert h.format_output(42) == "42"


# ---------------------------------------------------------------------------
# CLICommandHandler._log_info/_log_error/_record_metric
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCLICommandHandlerLoggingMetrics:
    def _handler(self, logger=None, metrics=None):
        return _ConcreteCLIHandler(
            query_bus=MagicMock(),
            command_bus=MagicMock(),
            logger=logger,
            metrics=metrics,
        )

    def test_log_info_no_op_when_no_logger(self):
        h = self._handler()
        h._log_info("msg")  # must not raise

    def test_log_info_calls_logger(self):
        logger = MagicMock()
        h = self._handler(logger=logger)
        h._log_info("msg %s", extra="v")
        logger.info.assert_called_once_with("msg %s", extra="v")

    def test_log_error_no_op_when_no_logger(self):
        h = self._handler()
        h._log_error("err")  # must not raise

    def test_log_error_calls_logger(self):
        logger = MagicMock()
        h = self._handler(logger=logger)
        h._log_error("err %s", extra="e")
        logger.error.assert_called_once_with("err %s", extra="e")

    def test_record_metric_no_op_when_no_metrics(self):
        h = self._handler()
        h._record_metric("m", 1)  # must not raise

    def test_record_metric_calls_metrics(self):
        metrics = MagicMock()
        h = self._handler(metrics=metrics)
        h._record_metric("m", 99, tag="v")
        metrics.record.assert_called_once_with("m", 99, tag="v")

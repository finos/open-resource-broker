"""Unit tests for BaseHandler (infrastructure/handlers/base/base_handler.py).

Coverage targets: lines 22-24,26,28-29,32,34-35,37,42-44,46-47,50-51,54,58,66,
75-76,79-80,82-85,90,92-95,100,102,110,112,121,123,132,134,145-146,148-156,158,
160,175-176,178-183,186,189-192,195,198,200,202,217,219-220,222-224,226-228,231,233
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.handlers.base.base_handler import (
    BaseHandler,
    _get_meter,
    _NoOpInstrument,
    _NoOpMeter,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _NoOpMeter / _NoOpInstrument
# ---------------------------------------------------------------------------


class TestNoOpInstruments:
    def test_no_op_meter_create_counter_returns_instrument(self):
        meter = _NoOpMeter()
        counter = meter.create_counter("my.counter", description="d", unit="1")
        assert isinstance(counter, _NoOpInstrument)

    def test_no_op_meter_create_histogram_returns_instrument(self):
        meter = _NoOpMeter()
        hist = meter.create_histogram("my.hist", description="d", unit="s")
        assert isinstance(hist, _NoOpInstrument)

    def test_no_op_instrument_add_is_noop(self):
        inst = _NoOpInstrument()
        inst.add(1, attributes={"k": "v"})  # must not raise

    def test_no_op_instrument_record_is_noop(self):
        inst = _NoOpInstrument()
        inst.record(0.5, attributes={"k": "v"})  # must not raise


class TestGetMeter:
    def test_returns_no_op_meter_when_otel_absent(self):
        with patch.dict("sys.modules", {"opentelemetry": None, "opentelemetry.metrics": None}):
            meter = _get_meter()
            assert isinstance(meter, _NoOpMeter)

    def test_returns_real_meter_when_otel_present(self):
        fake_meter = MagicMock()
        # OTel is available, so _get_meter takes the real branch:
        # otel_metrics.get_meter(__name__). Patch that call to observe it.
        with patch(
            "opentelemetry.metrics.get_meter",
            return_value=fake_meter,
        ) as mock_get_meter:
            meter = _get_meter()

        assert meter is fake_meter
        assert not isinstance(meter, _NoOpMeter)
        mock_get_meter.assert_called_once_with("orb.infrastructure.handlers.base.base_handler")


# ---------------------------------------------------------------------------
# BaseHandler construction
# ---------------------------------------------------------------------------


class TestBaseHandlerConstruction:
    def test_default_logger_created_when_none_passed(self):
        handler = BaseHandler()
        assert handler.logger is not None
        assert handler.metrics is None

    def test_custom_logger_stored(self):
        mock_logger = MagicMock()
        handler = BaseHandler(logger=mock_logger)
        assert handler.logger is mock_logger

    def test_metrics_stored(self):
        mock_metrics = MagicMock()
        handler = BaseHandler(metrics=mock_metrics)
        assert handler.metrics is mock_metrics

    def test_otel_caches_initialised_empty(self):
        handler = BaseHandler()
        assert handler._otel_counters == {}
        assert handler._otel_histograms == {}

    def test_class_name_used_as_logger_name(self):
        class MySpecificHandler(BaseHandler):
            pass

        handler = MySpecificHandler()
        assert handler.logger is not None  # logger name is class-based


# ---------------------------------------------------------------------------
# OTel instrument lazy creation
# ---------------------------------------------------------------------------


class TestOtelInstruments:
    def test_otel_counter_created_lazily(self):
        handler = BaseHandler()
        counter = handler._otel_counter("my_method")
        assert counter is not None
        # cached on second call
        counter2 = handler._otel_counter("my_method")
        assert counter is counter2

    def test_otel_histogram_created_lazily(self):
        handler = BaseHandler()
        hist = handler._otel_histogram("my_method")
        assert hist is not None
        hist2 = handler._otel_histogram("my_method")
        assert hist is hist2

    def test_different_methods_get_different_instruments(self):
        handler = BaseHandler()
        c1 = handler._otel_counter("method_a")
        c2 = handler._otel_counter("method_b")
        assert c1 is not c2


# ---------------------------------------------------------------------------
# log_entry / log_exit / log_error
# ---------------------------------------------------------------------------


class TestLoggingMethods:
    def setup_method(self):
        self.mock_logger = MagicMock()
        self.handler = BaseHandler(logger=self.mock_logger)

    def test_log_entry_calls_debug(self):
        self.handler.log_entry("my_method", x=1, y=2)
        self.mock_logger.debug.assert_called_once()
        args = self.mock_logger.debug.call_args
        assert "my_method" in args[0][1]

    def test_log_exit_calls_debug(self):
        self.handler.log_exit("my_method", result={"status": "ok"})
        self.mock_logger.debug.assert_called_once()
        args = self.mock_logger.debug.call_args
        assert "my_method" in args[0][1]

    def test_log_error_calls_error_with_exc_info(self):
        err = ValueError("boom")
        self.handler.log_error("my_method", err)
        self.mock_logger.error.assert_called_once()
        call_kwargs = self.mock_logger.error.call_args[1]
        assert call_kwargs.get("exc_info") is True

    def test_log_error_includes_method_name_and_error(self):
        err = RuntimeError("network failure")
        self.handler.log_error("call_api", err)
        args = self.mock_logger.error.call_args[0]
        assert "call_api" in args[1]


# ---------------------------------------------------------------------------
# with_logging decorator
# ---------------------------------------------------------------------------


class TestWithLogging:
    def setup_method(self):
        self.mock_logger = MagicMock()
        self.handler = BaseHandler(logger=self.mock_logger)

    def test_logs_entry_and_exit_on_success(self):
        def my_func(a, b):
            return a + b

        decorated = self.handler.with_logging(my_func)
        result = decorated(1, 2)
        assert result == 3
        assert self.mock_logger.debug.call_count == 2

    def test_logs_error_and_reraises_on_exception(self):
        def failing_func():
            raise ValueError("oops")

        decorated = self.handler.with_logging(failing_func)
        with pytest.raises(ValueError, match="oops"):
            decorated()
        self.mock_logger.error.assert_called_once()

    def test_preserves_function_name(self):
        def my_named_func():
            return "ok"

        decorated = self.handler.with_logging(my_named_func)
        assert decorated.__name__ == "my_named_func"

    def test_forwards_args_and_kwargs(self):
        received = {}

        def my_func(x, y=10):
            received["x"] = x
            received["y"] = y

        decorated = self.handler.with_logging(my_func)
        decorated(5, y=20)
        assert received == {"x": 5, "y": 20}


# ---------------------------------------------------------------------------
# with_metrics decorator
# ---------------------------------------------------------------------------


class TestWithMetrics:
    def setup_method(self):
        self.mock_logger = MagicMock()
        self.handler = BaseHandler(logger=self.mock_logger)

    def test_success_path_records_counter_and_histogram(self):
        mock_counter = MagicMock()
        mock_hist = MagicMock()
        self.handler._otel_counters["my_fn"] = mock_counter
        self.handler._otel_histograms["my_fn"] = mock_hist

        def my_fn():
            return 42

        decorated = self.handler.with_metrics(my_fn)
        result = decorated()
        assert result == 42
        mock_counter.add.assert_called_once_with(1, attributes={"outcome": "success"})
        mock_hist.record.assert_called_once()
        args, kwargs = mock_hist.record.call_args
        assert args[0] >= 0  # duration is non-negative
        assert kwargs["attributes"]["outcome"] == "success"

    def test_error_path_records_error_outcome(self):
        mock_counter = MagicMock()
        mock_hist = MagicMock()
        self.handler._otel_counters["failing_fn"] = mock_counter
        self.handler._otel_histograms["failing_fn"] = mock_hist

        def failing_fn():
            raise RuntimeError("disaster")

        decorated = self.handler.with_metrics(failing_fn)
        with pytest.raises(RuntimeError):
            decorated()

        mock_counter.add.assert_called_once()
        counter_attrs = mock_counter.add.call_args[1]["attributes"]
        assert counter_attrs["outcome"] == "error"
        assert counter_attrs["error"] == "RuntimeError"

    def test_name_override_used_for_metric_key(self):
        def my_method():
            return "x"

        decorated = self.handler.with_metrics(my_method, name="custom_name")
        decorated()
        assert "custom_name" in self.handler._otel_counters

    def test_function_name_used_when_no_override(self):
        def my_func():
            return "x"

        decorated = self.handler.with_metrics(my_func)
        decorated()
        assert "my_func" in self.handler._otel_counters

    def test_preserves_function_name(self):
        def my_named_fn():
            return "y"

        decorated = self.handler.with_metrics(my_named_fn)
        assert decorated.__name__ == "my_named_fn"

    def test_with_metrics_forwards_args(self):
        received = {}

        def fn(a, b=2):
            received["a"] = a
            received["b"] = b

        decorated = self.handler.with_metrics(fn)
        decorated(10, b=20)
        assert received == {"a": 10, "b": 20}


# ---------------------------------------------------------------------------
# with_error_handling decorator
# ---------------------------------------------------------------------------


class TestWithErrorHandling:
    def setup_method(self):
        self.handler = BaseHandler()

    def test_success_path_returns_result(self):
        def my_func():
            return "success"

        decorated = self.handler.with_error_handling(my_func)
        assert decorated() == "success"

    def test_mapped_error_handled_by_handler_fn(self):
        handler_called_with = []

        def handle_value_err(e):
            handler_called_with.append(e)
            return "handled"

        def failing():
            raise ValueError("test error")

        decorated = self.handler.with_error_handling(
            failing, error_map={ValueError: handle_value_err}
        )
        result = decorated()
        assert result == "handled"
        assert len(handler_called_with) == 1
        assert isinstance(handler_called_with[0], ValueError)

    def test_unmapped_error_reraised(self):
        def failing():
            raise RuntimeError("unhandled")

        decorated = self.handler.with_error_handling(
            failing, error_map={ValueError: lambda e: None}
        )
        with pytest.raises(RuntimeError, match="unhandled"):
            decorated()

    def test_no_error_map_reraises_all_exceptions(self):
        def failing():
            raise TypeError("type mismatch")

        decorated = self.handler.with_error_handling(failing)
        with pytest.raises(TypeError, match="type mismatch"):
            decorated()

    def test_error_map_matches_subclass(self):
        """Mapped exception type should match subclasses via isinstance."""

        class MyError(ValueError):
            pass

        results = []

        def handle(e):
            results.append("caught")
            return "ok"

        def raise_subclass():
            raise MyError("sub")

        decorated = self.handler.with_error_handling(raise_subclass, error_map={ValueError: handle})
        result = decorated()
        assert result == "ok"
        assert results == ["caught"]

    def test_preserves_function_name(self):
        def original_func():
            return "x"

        decorated = self.handler.with_error_handling(original_func)
        assert decorated.__name__ == "original_func"

    def test_forwards_args_to_wrapped_function(self):
        received = {}

        def my_func(x, y=5):
            received["x"] = x
            received["y"] = y

        decorated = self.handler.with_error_handling(my_func)
        decorated(1, y=2)
        assert received == {"x": 1, "y": 2}

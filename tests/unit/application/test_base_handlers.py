"""Unit tests for application/base/handlers.py — BaseHandler, BaseCommandHandler, BaseQueryHandler, BaseProviderHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.base.handlers import (
    BaseCommandHandler,
    BaseHandler,
    BaseProviderHandler,
    BaseQueryHandler,
    _NoOpLogger,
)
from orb.domain.base.ports import ErrorHandlingPort, EventPublisherPort, LoggingPort

# ---------------------------------------------------------------------------
# Concrete minimal subclasses for abstract base testing
# ---------------------------------------------------------------------------


class _ConcreteCommandHandler(BaseCommandHandler):
    """Minimal concrete command handler for testing."""

    async def execute_command(self, command):
        return None


class _ConcreteCommandHandlerReturnsData(BaseCommandHandler):
    """Command handler whose result has an events list."""

    def __init__(self, events=None, **kwargs):
        super().__init__(**kwargs)
        self._events_to_return = events or []

    async def execute_command(self, command):
        result = MagicMock()
        result.events = self._events_to_return
        return result


class _ConcreteQueryHandler(BaseQueryHandler):
    """Minimal concrete query handler for testing."""

    def __init__(self, response=None, **kwargs):
        super().__init__(**kwargs)
        self._response = response

    async def execute_query(self, query):
        return self._response


class _ConcreteProviderHandler(BaseProviderHandler):
    """Minimal concrete provider handler for testing."""

    def __init__(self, side_effect=None, **kwargs):
        super().__init__(**kwargs)
        self._side_effect = side_effect
        self._call_count = 0

    async def execute_provider_operation(self, operation, **kwargs):
        self._call_count += 1
        if self._side_effect:
            raise self._side_effect
        return {"ok": True}


class _ConcreteBaseHandler(BaseHandler):
    """Minimal concrete base handler — only BaseHandler requires no abstract methods."""

    pass


# ---------------------------------------------------------------------------
# _NoOpLogger
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoOpLogger:
    def test_all_methods_exist_and_do_not_raise(self):
        logger = _NoOpLogger()
        logger.debug("msg")
        logger.info("msg")
        logger.warning("msg")
        logger.error("msg")
        logger.critical("msg")
        logger.exception("msg")
        logger.log(10, "msg")


# ---------------------------------------------------------------------------
# BaseHandler — initialisation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseHandlerInit:
    def test_uses_noop_logger_when_none_provided(self):
        handler = _ConcreteBaseHandler()
        assert isinstance(handler.logger, _NoOpLogger)

    def test_uses_provided_logger(self):
        logger = MagicMock(spec=LoggingPort)
        handler = _ConcreteBaseHandler(logger=logger)
        assert handler.logger is logger

    def test_error_handler_stored(self):
        eh = MagicMock(spec=ErrorHandlingPort)
        handler = _ConcreteBaseHandler(error_handler=eh)
        assert handler.error_handler is eh

    def test_metrics_dict_initially_empty(self):
        handler = _ConcreteBaseHandler()
        assert handler.get_metrics() == {}


# ---------------------------------------------------------------------------
# BaseHandler — handle_with_error_management
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleWithErrorManagement:
    @pytest.mark.asyncio
    async def test_executes_operation_without_error_handler(self):
        handler = _ConcreteBaseHandler()

        async def op():
            return 42

        result = await handler.handle_with_error_management(op)
        assert result == 42

    @pytest.mark.asyncio
    async def test_reraises_exception_without_error_handler(self):
        handler = _ConcreteBaseHandler()

        async def op():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await handler.handle_with_error_management(op)

    @pytest.mark.asyncio
    async def test_reraises_exception_and_logs_without_error_handler(self):
        logger = MagicMock(spec=LoggingPort)
        handler = _ConcreteBaseHandler(logger=logger)

        async def op():
            raise ValueError("oops")

        with pytest.raises(ValueError):
            await handler.handle_with_error_management(op, context="ctx")

        logger.error.assert_called()


# ---------------------------------------------------------------------------
# BaseHandler — handle_error
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleError:
    def test_handle_error_logs_and_reraises(self):
        logger = MagicMock(spec=LoggingPort)
        handler = _ConcreteBaseHandler(logger=logger)
        err = RuntimeError("test error")

        with pytest.raises(RuntimeError, match="test error"):
            handler.handle_error(err, context="my_context")

        logger.error.assert_called()


# ---------------------------------------------------------------------------
# BaseHandler — with_monitoring decorator
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWithMonitoringDecorator:
    @pytest.mark.asyncio
    async def test_monitoring_records_success_metrics(self):
        handler = _ConcreteBaseHandler()

        @handler.with_monitoring("my_op")
        async def my_op():
            return "result"

        result = await my_op()
        assert result == "result"
        metrics = handler.get_metrics()
        key = "_ConcreteBaseHandler.my_op"
        assert key in metrics
        assert metrics[key]["status"] == "success"

    @pytest.mark.asyncio
    async def test_monitoring_records_error_metrics_on_exception(self):
        handler = _ConcreteBaseHandler()

        @handler.with_monitoring("failing_op")
        async def failing_op():
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await failing_op()

        metrics = handler.get_metrics()
        key = "_ConcreteBaseHandler.failing_op"
        assert key in metrics
        assert metrics[key]["status"] == "error"
        assert "fail" in metrics[key]["error"]


# ---------------------------------------------------------------------------
# BaseCommandHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseCommandHandler:
    def test_event_publisher_stored(self):
        ep = MagicMock(spec=EventPublisherPort)
        handler = _ConcreteCommandHandler(event_publisher=ep)
        assert handler.event_publisher is ep

    @pytest.mark.asyncio
    async def test_handle_raises_when_command_none(self):
        handler = _ConcreteCommandHandler()
        with pytest.raises(ValueError, match="cannot be None"):
            await handler.handle(None)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_handle_invokes_execute_command(self):
        calls = []

        class _Tracking(_ConcreteCommandHandler):
            async def execute_command(self, command):
                calls.append(command)
                return None

        handler = _Tracking()
        cmd = MagicMock()
        await handler.handle(cmd)
        assert cmd in calls

    @pytest.mark.asyncio
    async def test_publish_events_called_when_result_has_events(self):
        ev1 = MagicMock()
        ep = MagicMock(spec=EventPublisherPort)

        handler = _ConcreteCommandHandlerReturnsData(events=[ev1], event_publisher=ep)
        cmd = MagicMock()
        await handler.handle(cmd)
        ep.publish.assert_called_once_with(ev1)

    @pytest.mark.asyncio
    async def test_publish_events_skipped_when_result_is_none(self):
        ep = MagicMock(spec=EventPublisherPort)
        handler = _ConcreteCommandHandler(event_publisher=ep)
        cmd = MagicMock()
        await handler.handle(cmd)
        ep.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_events_noop_without_event_publisher(self):
        """publish_events must not raise when event_publisher is None."""
        handler = _ConcreteCommandHandler()
        # Should not raise
        await handler.publish_events([MagicMock()])


# ---------------------------------------------------------------------------
# BaseQueryHandler — caching
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseQueryHandler:
    @pytest.mark.asyncio
    async def test_execute_query_called_for_non_cached_query(self):
        handler = _ConcreteQueryHandler(response={"data": 1})
        result = await handler.handle(MagicMock())
        assert result == {"data": 1}

    @pytest.mark.asyncio
    async def test_cache_hit_skips_execute_query(self):
        call_count = 0

        class _Counting(_ConcreteQueryHandler):
            def get_cache_key(self, query):
                return "fixed-key"

            def is_cacheable(self, query, result):
                return True

            async def execute_query(self, query):
                nonlocal call_count
                call_count += 1
                return {"val": call_count}

        handler = _Counting()
        r1 = await handler.handle(MagicMock())
        r2 = await handler.handle(MagicMock())
        assert r1 == r2
        assert call_count == 1  # second call hit cache

    @pytest.mark.asyncio
    async def test_query_failure_is_logged_and_reraised(self):
        logger = MagicMock(spec=LoggingPort)

        class _Failing(_ConcreteQueryHandler):
            async def execute_query(self, query):
                raise RuntimeError("query fail")

        handler = _Failing(logger=logger)
        with pytest.raises(RuntimeError, match="query fail"):
            await handler.handle(MagicMock())

        logger.error.assert_called()

    def test_get_cache_key_returns_none_by_default(self):
        handler = _ConcreteQueryHandler()
        assert handler.get_cache_key(MagicMock()) is None

    def test_is_cacheable_returns_false_by_default(self):
        handler = _ConcreteQueryHandler()
        assert handler.is_cacheable(MagicMock(), MagicMock()) is False


# ---------------------------------------------------------------------------
# BaseProviderHandler — retry logic
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseProviderHandler:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self):
        handler = _ConcreteProviderHandler()
        result = await handler.handle_provider_operation("describe")
        assert result == {"ok": True}
        assert handler._call_count == 1

    @pytest.mark.asyncio
    async def test_retries_up_to_max_then_raises(self):
        handler = _ConcreteProviderHandler(side_effect=RuntimeError("transient"))
        handler.retry_delay = 0.0  # zero delay for test speed
        with patch("orb.application.base.handlers.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(RuntimeError, match="transient"):
                await handler.handle_provider_operation("describe")

        # max_retries=3 means 4 total attempts (0,1,2,3)
        assert handler._call_count == handler.max_retries + 1

    @pytest.mark.asyncio
    async def test_retry_logs_warning_before_last_attempt(self):
        logger = MagicMock(spec=LoggingPort)
        handler = _ConcreteProviderHandler(side_effect=RuntimeError("err"), logger=logger)
        handler.retry_delay = 0.0

        with patch("orb.application.base.handlers.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(RuntimeError):
                await handler.handle_provider_operation("op")

        # Should have warning calls for intermediate failures
        assert logger.warning.call_count >= 1

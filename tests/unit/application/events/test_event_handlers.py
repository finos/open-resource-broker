"""Unit tests for application event handlers — machine, request, system, infrastructure."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.base.event_handlers import BaseEventHandler, BaseLoggingEventHandler
from orb.application.events.base.event_handler import EventHandler
from orb.application.events.base.logging_event_handler import LoggingEventHandler
from orb.application.events.handlers.infrastructure_handlers import (
    CacheOperationHandler,
    DatabaseConnectionHandler,
)
from orb.application.events.handlers.machine_handlers import (
    MachineCreatedHandler,
    MachineErrorHandler,
    MachineHealthCheckHandler,
    MachineStatusUpdatedHandler,
    MachineTerminatedHandler,
)
from orb.application.events.handlers.request_handlers import (
    RequestCancelledHandler,
    RequestCompletedHandler,
    RequestCreatedHandler,
    RequestFailedHandler,
    RequestStatusUpdatedHandler,
    RequestTimeoutHandler,
)
from orb.application.events.handlers.system_handlers import (
    ConfigurationUpdatedHandler,
    SystemShutdownHandler,
    SystemStartedHandler,
)
from orb.domain.base.events.base_events import DomainEvent
from orb.domain.base.ports import LoggingPort

# ---------------------------------------------------------------------------
# DomainEvent factory helpers
# ---------------------------------------------------------------------------


def _event(
    event_type: str = "TestEvent",
    aggregate_id: str = "agg-1",
    aggregate_type: str = "TestAgg",
    **extra,
) -> DomainEvent:
    return DomainEvent(
        aggregate_id=aggregate_id,
        aggregate_type=aggregate_type,
        event_type=event_type,
        **extra,
    )


def _machine_event(**kwargs) -> DomainEvent:
    return _event(aggregate_type="Machine", **kwargs)


def _request_event(**kwargs) -> DomainEvent:
    return _event(aggregate_type="Request", **kwargs)


# ---------------------------------------------------------------------------
# EventHandler base — retry logic & dead letter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEventHandlerBase:
    @pytest.mark.asyncio
    async def test_process_with_retry_succeeds_on_first_attempt(self):
        """process_event called once when it succeeds immediately."""
        calls = []

        class _OK(EventHandler):
            async def process_event(self, event):
                calls.append(event)

        h = _OK()
        h.retry_count = 3
        h.retry_delay = 0.0
        ev = _event()
        await h.handle(ev)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_process_with_retry_retries_on_failure_then_raises(self):
        """Failing process_event is retried retry_count times then raises."""
        calls = []

        class _Failing(EventHandler):
            async def process_event(self, event):
                calls.append(1)
                raise RuntimeError("fail")

        h = _Failing()
        h.retry_count = 2
        h.retry_delay = 0.0

        with pytest.raises(RuntimeError, match="fail"):
            await h.handle(_event())

        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_handle_logs_success_when_logger_provided(self):
        logger = MagicMock(spec=LoggingPort)

        class _OK(EventHandler):
            async def process_event(self, event):
                pass

        h = _OK(logger=logger)
        h.retry_count = 1
        h.retry_delay = 0.0
        await h.handle(_event())
        logger.debug.assert_called()

    def test_format_duration_ms_below_1000(self):
        class _OK(EventHandler):
            async def process_event(self, event):
                pass

        h = _OK()
        assert h.format_duration(500.0) == "500.0ms"

    def test_format_duration_seconds_above_1000(self):
        class _OK(EventHandler):
            async def process_event(self, event):
                pass

        h = _OK()
        result = h.format_duration(2000.0)
        assert "s" in result
        assert "2.00" in result

    def test_extract_fields_from_event_attributes(self):
        class _OK(EventHandler):
            async def process_event(self, event):
                pass

        h = _OK()
        ev = _event(aggregate_id="x99")
        fields = h.extract_fields(ev, {"aggregate_id": "default"})
        assert fields["aggregate_id"] == "x99"

    def test_extract_fields_uses_default_for_missing(self):
        class _OK(EventHandler):
            async def process_event(self, event):
                pass

        h = _OK()
        fields = h.extract_fields(_event(), {"nonexistent_field": "fallback"})
        assert fields["nonexistent_field"] == "fallback"

    def test_format_status_change_without_reason(self):
        class _OK(EventHandler):
            async def process_event(self, event):
                pass

        h = _OK()
        result = h.format_status_change("pending", "running")
        assert "pending" in result
        assert "running" in result

    def test_format_status_change_with_reason(self):
        class _OK(EventHandler):
            async def process_event(self, event):
                pass

        h = _OK()
        result = h.format_status_change("pending", "running", reason="user")
        assert "user" in result


# ---------------------------------------------------------------------------
# LoggingEventHandler base
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoggingEventHandlerBase:
    @pytest.mark.asyncio
    async def test_process_event_calls_format_message_and_logs(self):
        logger = MagicMock(spec=LoggingPort)

        class _Concrete(LoggingEventHandler):
            def format_message(self, event):
                return "formatted-msg"

        h = _Concrete(logger=logger)
        h.retry_count = 1
        h.retry_delay = 0.0
        await h.handle(_event())
        logger.info.assert_called_with("formatted-msg")

    def test_format_basic_message_structure(self):
        class _Concrete(LoggingEventHandler):
            def format_message(self, event):
                return ""

        h = _Concrete()
        msg = h.format_basic_message(
            _event(aggregate_id="agg-5", aggregate_type="Widget"), "created"
        )
        assert "Widget" in msg
        assert "agg-5" in msg
        assert "created" in msg

    def test_format_basic_message_with_details(self):
        class _Concrete(LoggingEventHandler):
            def format_message(self, event):
                return ""

        h = _Concrete()
        msg = h.format_basic_message(_event(), "processed", details="extra info")
        assert "extra info" in msg

    def test_format_status_change_message(self):
        class _Concrete(LoggingEventHandler):
            def format_message(self, event):
                return ""

        h = _Concrete()
        msg = h.format_status_change_message(_event(), "pending", "running")
        assert "pending" in msg
        assert "running" in msg

    def test_format_error_message(self):
        class _Concrete(LoggingEventHandler):
            def format_message(self, event):
                return ""

        h = _Concrete()
        msg = h.format_error_message(_event(), "connection refused")
        assert "connection refused" in msg


# ---------------------------------------------------------------------------
# MachineCreatedHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineCreatedHandler:
    @pytest.mark.asyncio
    async def test_format_includes_template_and_instance_type(self):
        h = MachineCreatedHandler()
        ev = _machine_event(aggregate_id="m-1", template_id="tmpl-x", instance_type="t3.micro")
        # Attach extra attrs as a simple namespace
        ev = MagicMock()
        ev.event_id = "eid"
        ev.event_type = "MachineCreatedEvent"
        ev.aggregate_id = "m-1"
        ev.aggregate_type = "Machine"
        ev.template_id = "tmpl-x"
        ev.instance_type = "t3.micro"
        ev.availability_zone = None

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "m-1" in msg
        assert "tmpl-x" in msg
        assert "t3.micro" in msg

    @pytest.mark.asyncio
    async def test_format_includes_az_when_present(self):
        h = MachineCreatedHandler()
        ev = MagicMock()
        ev.aggregate_id = "m-2"
        ev.template_id = "tmpl-y"
        ev.instance_type = "c5.large"
        ev.availability_zone = "us-east-1a"

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "us-east-1a" in msg


# ---------------------------------------------------------------------------
# MachineStatusUpdatedHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineStatusUpdatedHandler:
    @pytest.mark.asyncio
    async def test_format_includes_old_and_new_status(self):
        h = MachineStatusUpdatedHandler()
        ev = MagicMock()
        ev.aggregate_id = "m-3"
        ev.old_status = "pending"
        ev.new_status = "running"

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "pending" in msg
        assert "running" in msg


# ---------------------------------------------------------------------------
# MachineTerminatedHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineTerminatedHandler:
    @pytest.mark.asyncio
    async def test_format_includes_reason(self):
        h = MachineTerminatedHandler()
        ev = MagicMock()
        ev.aggregate_id = "m-4"
        ev.termination_reason = "user_requested"

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "user_requested" in msg

    def test_log_level_info_for_user_requested(self):
        h = MachineTerminatedHandler()
        ev = MagicMock()
        ev.termination_reason = "user_requested"
        assert h.get_log_level(ev) == "info"  # type: ignore[attr-defined]

    def test_log_level_info_for_scheduled(self):
        h = MachineTerminatedHandler()
        ev = MagicMock()
        ev.termination_reason = "scheduled"
        assert h.get_log_level(ev) == "info"  # type: ignore[attr-defined]

    def test_log_level_warning_for_unexpected_reason(self):
        h = MachineTerminatedHandler()
        ev = MagicMock()
        ev.termination_reason = "out_of_memory"
        assert h.get_log_level(ev) == "warning"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# MachineHealthCheckHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineHealthCheckHandler:
    @pytest.mark.asyncio
    async def test_format_includes_health_status(self):
        h = MachineHealthCheckHandler()
        ev = MagicMock()
        ev.aggregate_id = "m-5"
        ev.health_status = "healthy"
        ev.check_type = "ec2"

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "healthy" in msg
        assert "ec2" in msg

    def test_log_level_debug_for_healthy(self):
        h = MachineHealthCheckHandler()
        ev = MagicMock()
        ev.health_status = "healthy"
        assert h.get_log_level(ev) == "debug"  # type: ignore[attr-defined]

    def test_log_level_warning_for_unhealthy(self):
        h = MachineHealthCheckHandler()
        ev = MagicMock()
        ev.health_status = "unhealthy"
        assert h.get_log_level(ev) == "warning"  # type: ignore[attr-defined]

    def test_log_level_info_for_unknown_status(self):
        h = MachineHealthCheckHandler()
        ev = MagicMock()
        ev.health_status = "degraded"
        assert h.get_log_level(ev) == "info"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# MachineErrorHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineErrorHandler:
    @pytest.mark.asyncio
    async def test_format_includes_error_type_and_message(self):
        h = MachineErrorHandler()
        ev = MagicMock()
        ev.aggregate_id = "m-6"
        ev.error_type = "ProvisionError"
        ev.error_message = "capacity exceeded"

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "ProvisionError" in msg
        assert "capacity exceeded" in msg

    def test_log_level_is_error(self):
        h = MachineErrorHandler()
        assert h.get_log_level(MagicMock()) == "error"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# RequestCreatedHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestCreatedHandler:
    @pytest.mark.asyncio
    async def test_format_includes_template_and_count(self):
        h = RequestCreatedHandler()
        ev = MagicMock()
        ev.aggregate_id = "req-1"
        ev.template_id = "tmpl-z"
        ev.machine_count = 5

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "tmpl-z" in msg
        assert "5" in msg


# ---------------------------------------------------------------------------
# RequestStatusUpdatedHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestStatusUpdatedHandler:
    @pytest.mark.asyncio
    async def test_format_includes_old_and_new_status(self):
        h = RequestStatusUpdatedHandler()
        ev = MagicMock()
        ev.aggregate_id = "req-2"
        ev.old_status = "pending"
        ev.new_status = "in_progress"

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "pending" in msg
        assert "in_progress" in msg


# ---------------------------------------------------------------------------
# RequestCompletedHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestCompletedHandler:
    @pytest.mark.asyncio
    async def test_format_includes_duration_and_machine_count(self):
        h = RequestCompletedHandler()
        ev = MagicMock()
        ev.aggregate_id = "req-3"
        ev.completion_duration = 42
        ev.machines_created = 3

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "42" in msg
        assert "3" in msg


# ---------------------------------------------------------------------------
# RequestFailedHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestFailedHandler:
    @pytest.mark.asyncio
    async def test_format_includes_failure_reason(self):
        h = RequestFailedHandler()
        ev = MagicMock()
        ev.aggregate_id = "req-4"
        ev.failure_reason = "capacity_unavailable"

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "capacity_unavailable" in msg

    def test_log_level_is_error(self):
        h = RequestFailedHandler()
        assert h.get_log_level(MagicMock()) == "error"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# RequestCancelledHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestCancelledHandler:
    @pytest.mark.asyncio
    async def test_format_includes_cancellation_reason(self):
        h = RequestCancelledHandler()
        ev = MagicMock()
        ev.aggregate_id = "req-5"
        ev.cancellation_reason = "budget_exceeded"

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "budget_exceeded" in msg

    def test_log_level_is_warning(self):
        h = RequestCancelledHandler()
        assert h.get_log_level(MagicMock()) == "warning"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# RequestTimeoutHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestTimeoutHandler:
    @pytest.mark.asyncio
    async def test_format_includes_timeout_duration(self):
        h = RequestTimeoutHandler()
        ev = MagicMock()
        ev.aggregate_id = "req-6"
        ev.timeout_duration = 3600

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "3600" in msg

    def test_log_level_is_error(self):
        h = RequestTimeoutHandler()
        assert h.get_log_level(MagicMock()) == "error"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# SystemStartedHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSystemStartedHandler:
    @pytest.mark.asyncio
    async def test_format_includes_version_and_startup_time(self):
        h = SystemStartedHandler()
        ev = MagicMock()
        ev.version = "1.2.3"
        ev.startup_time = 0.5

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "1.2.3" in msg
        assert "0.5" in msg

    def test_log_level_is_info(self):
        h = SystemStartedHandler()
        assert h.get_log_level(MagicMock()) == "info"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# SystemShutdownHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSystemShutdownHandler:
    @pytest.mark.asyncio
    async def test_format_includes_reason_and_graceful_flag(self):
        h = SystemShutdownHandler()
        ev = MagicMock()
        ev.shutdown_reason = "sigterm"
        ev.graceful_shutdown = True

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "sigterm" in msg
        assert "graceful" in msg.lower()

    def test_log_level_info_for_graceful(self):
        h = SystemShutdownHandler()
        ev = MagicMock()
        ev.graceful_shutdown = True
        assert h.get_log_level(ev) == "info"  # type: ignore[attr-defined]

    def test_log_level_warning_for_forced(self):
        h = SystemShutdownHandler()
        ev = MagicMock()
        ev.graceful_shutdown = False
        assert h.get_log_level(ev) == "warning"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# ConfigurationUpdatedHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigurationUpdatedHandler:
    @pytest.mark.asyncio
    async def test_format_includes_section_and_keys(self):
        h = ConfigurationUpdatedHandler()
        ev = MagicMock()
        ev.config_section = "database"
        ev.changed_keys = ["host", "port"]

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "database" in msg
        assert "host" in msg
        assert "port" in msg

    @pytest.mark.asyncio
    async def test_format_with_empty_changed_keys(self):
        h = ConfigurationUpdatedHandler()
        ev = MagicMock()
        ev.config_section = "cache"
        ev.changed_keys = []

        msg = await h.format_log_message(ev)  # type: ignore[attr-defined]
        assert "cache" in msg

    def test_log_level_is_info(self):
        h = ConfigurationUpdatedHandler()
        assert h.get_log_level(MagicMock()) == "info"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# DatabaseConnectionHandler (infrastructure)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDatabaseConnectionHandler:
    def test_format_message_includes_status_and_type(self):
        h = DatabaseConnectionHandler()
        ev = MagicMock()
        ev.connection_status = "connected"
        ev.database_type = "postgres"
        ev.connection_time = None
        ev.retry_count = 0

        # format_message is called directly (sync)
        msg = h.format_message(ev)  # type: ignore[attr-defined]
        assert "connected" in msg
        assert "postgres" in msg

    def test_format_message_includes_retries_when_nonzero(self):
        h = DatabaseConnectionHandler()
        ev = MagicMock()
        ev.connection_status = "reconnected"
        ev.database_type = "dynamodb"
        ev.connection_time = None
        ev.retry_count = 3

        msg = h.format_message(ev)  # type: ignore[attr-defined]
        assert "3" in msg

    def test_format_message_includes_duration_when_present(self):
        h = DatabaseConnectionHandler()
        ev = MagicMock()
        ev.connection_status = "connected"
        ev.database_type = "postgres"
        ev.connection_time = 150.0
        ev.retry_count = 0

        msg = h.format_message(ev)  # type: ignore[attr-defined]
        assert "ms" in msg or "s" in msg


# ---------------------------------------------------------------------------
# CacheOperationHandler (infrastructure)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCacheOperationHandler:
    def test_format_message_includes_operation_and_key(self):
        h = CacheOperationHandler()
        ev = MagicMock()
        ev.operation = "get"
        ev.cache_key = "session:abc"
        ev.hit_rate = None
        ev.operation_time = None

        msg = h.format_message(ev)  # type: ignore[attr-defined]
        assert "get" in msg
        assert "session:abc" in msg

    def test_format_message_includes_hit_rate_when_present(self):
        h = CacheOperationHandler()
        ev = MagicMock()
        ev.operation = "get"
        ev.cache_key = "k"
        ev.hit_rate = 85.5
        ev.operation_time = None

        msg = h.format_message(ev)  # type: ignore[attr-defined]
        assert "85.5" in msg


# ---------------------------------------------------------------------------
# BaseEventHandler (base/event_handlers.py) — full handle() path
# ---------------------------------------------------------------------------


def _make_domain_event(aggregate_id: str = "agg-1") -> "DomainEvent":
    return DomainEvent(
        aggregate_id=aggregate_id,
        aggregate_type="Test",
        event_type="TestEvent",
    )


@pytest.mark.unit
class TestBaseEventHandlerHandle:
    @pytest.mark.asyncio
    async def test_handle_success_records_success_metrics(self):
        class _Concrete(BaseEventHandler):
            async def execute_event(self, event):
                pass

        h = _Concrete()
        ev = _make_domain_event()
        await h.handle(ev)
        metrics = h.get_metrics()
        # metrics key is the event class name returned by event.__class__.__name__
        # DomainEvent → "DomainEvent"
        key = ev.__class__.__name__
        assert key in metrics
        assert metrics[key]["success_count"] == 1

    @pytest.mark.asyncio
    async def test_handle_failure_records_failure_metrics_and_reraises(self):
        class _Failing(BaseEventHandler):
            async def execute_event(self, event):
                raise ValueError("boom")

        h = _Failing()
        ev = _make_domain_event()
        with pytest.raises(ValueError):
            await h.handle(ev)

        metrics = h.get_metrics()
        key = ev.__class__.__name__
        assert key in metrics
        assert metrics[key]["failure_count"] == 1
        assert "boom" in metrics[key]["last_error"]

    @pytest.mark.asyncio
    async def test_validate_event_raises_when_missing_event_id(self):
        class _Concrete(BaseEventHandler):
            async def execute_event(self, event):
                pass

        h = _Concrete()
        bad_ev = MagicMock(spec=[])  # no event_id attribute
        with pytest.raises((ValueError, AttributeError)):
            await h.validate_event(bad_ev)

    @pytest.mark.asyncio
    async def test_validate_event_raises_when_none(self):
        class _Concrete(BaseEventHandler):
            async def execute_event(self, event):
                pass

        h = _Concrete()
        with pytest.raises(ValueError):
            await h.validate_event(None)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_handle_calls_error_handler_on_failure(self):
        from unittest.mock import AsyncMock as AM

        class _Failing(BaseEventHandler):
            async def execute_event(self, event):
                raise RuntimeError("err")

        eh = MagicMock()
        # handle_error is awaited in BaseEventHandler.handle
        eh.handle_error = AM(return_value=None)
        h = _Failing(error_handler=eh)
        ev = _make_domain_event()
        with pytest.raises(RuntimeError):
            await h.handle(ev)
        eh.handle_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publish_cascading_events_calls_event_publisher(self):
        class _Concrete(BaseEventHandler):
            async def execute_event(self, event):
                pass

        from unittest.mock import AsyncMock as AM

        ep = MagicMock()
        ep.publish = AM()
        h = _Concrete(event_publisher=ep)
        ev = _make_domain_event()
        await h.publish_cascading_events([ev])
        ep.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_avg_duration_computed_after_two_successes(self):
        class _Concrete(BaseEventHandler):
            async def execute_event(self, event):
                pass

        h = _Concrete()
        ev = _make_domain_event()
        await h.handle(ev)
        await h.handle(ev)
        metrics = h.get_metrics()
        key = ev.__class__.__name__
        assert metrics[key]["success_count"] == 2
        assert metrics[key]["avg_duration"] >= 0


# ---------------------------------------------------------------------------
# BaseLoggingEventHandler — log level routing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseLoggingEventHandlerLogLevelRouting:
    @pytest.mark.asyncio
    async def test_debug_log_level_uses_debug_method(self):
        logger = MagicMock(spec=LoggingPort)

        class _Debug(BaseLoggingEventHandler):
            def get_log_level(self, event):
                return "debug"

            async def format_log_message(self, event):
                return "debug msg"

        h = _Debug(logger=logger)
        ev = _make_domain_event()
        await h.handle(ev)
        logger.debug.assert_any_call("debug msg")

    @pytest.mark.asyncio
    async def test_warning_log_level_uses_warning_method(self):
        logger = MagicMock(spec=LoggingPort)

        class _Warn(BaseLoggingEventHandler):
            def get_log_level(self, event):
                return "warning"

            async def format_log_message(self, event):
                return "warn msg"

        h = _Warn(logger=logger)
        ev = _make_domain_event()
        await h.handle(ev)
        logger.warning.assert_any_call("warn msg")

    @pytest.mark.asyncio
    async def test_error_log_level_uses_error_method(self):
        logger = MagicMock(spec=LoggingPort)

        class _Err(BaseLoggingEventHandler):
            def get_log_level(self, event):
                return "error"

            async def format_log_message(self, event):
                return "error msg"

        h = _Err(logger=logger)
        ev = _make_domain_event()
        await h.handle(ev)
        logger.error.assert_any_call("error msg")

    @pytest.mark.asyncio
    async def test_unknown_log_level_defaults_to_info(self):
        logger = MagicMock(spec=LoggingPort)

        class _Unknown(BaseLoggingEventHandler):
            def get_log_level(self, event):
                return "trace"  # Not one of the recognised levels

            async def format_log_message(self, event):
                return "trace msg"

        h = _Unknown(logger=logger)
        ev = _make_domain_event()
        await h.handle(ev)
        logger.info.assert_any_call("trace msg")

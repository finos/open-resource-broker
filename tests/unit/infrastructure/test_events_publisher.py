"""Unit tests for events/publisher.py."""

from unittest.mock import MagicMock

import pytest

from orb.domain.base.events.base_events import DomainEvent
from orb.infrastructure.events.publisher import (
    ConfigurableEventPublisher,
    create_event_publisher,
)


def _make_event(event_type: str = "TestEvent") -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        aggregate_id="agg-1",
        aggregate_type="Machine",
    )


@pytest.mark.unit
class TestConfigurableEventPublisherInit:
    """Tests for constructor and mode validation."""

    def test_logging_mode_accepted(self) -> None:
        pub = ConfigurableEventPublisher(mode="logging")
        assert pub.mode == "logging"

    def test_sync_mode_accepted(self) -> None:
        pub = ConfigurableEventPublisher(mode="sync")
        assert pub.mode == "sync"

    def test_async_mode_accepted(self) -> None:
        pub = ConfigurableEventPublisher(mode="async")
        assert pub.mode == "async"

    def test_invalid_mode_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid mode"):
            ConfigurableEventPublisher(mode="kafka")

    def test_default_mode_is_logging(self) -> None:
        pub = ConfigurableEventPublisher()
        assert pub.mode == "logging"


@pytest.mark.unit
class TestConfigurableEventPublisherPublishLogging:
    """Tests for logging mode publish."""

    def test_publish_logging_mode_does_not_raise(self) -> None:
        pub = ConfigurableEventPublisher(mode="logging")
        pub.publish(_make_event())  # should not raise

    def test_publish_logging_mode_logs_event(self) -> None:
        pub = ConfigurableEventPublisher(mode="logging")
        mock_logger = MagicMock()
        pub._logger = mock_logger
        event = _make_event("MachineCreated")
        pub.publish(event)
        mock_logger.info.assert_called_once()

    def test_exception_in_log_does_not_propagate(self) -> None:
        pub = ConfigurableEventPublisher(mode="logging")
        mock_logger = MagicMock()
        mock_logger.info.side_effect = RuntimeError("log broken")
        pub._logger = mock_logger
        pub.publish(_make_event())  # must not raise


@pytest.mark.unit
class TestConfigurableEventPublisherPublishSync:
    """Tests for sync mode with handler registration."""

    def test_registered_handler_called_on_publish(self) -> None:
        pub = ConfigurableEventPublisher(mode="sync")
        received: list[DomainEvent] = []

        def handler(e: DomainEvent) -> None:
            received.append(e)

        pub.register_handler("MachineReady", handler)
        event = _make_event("MachineReady")
        pub.publish(event)
        assert len(received) == 1
        assert received[0] is event

    def test_no_handler_registered_does_not_raise(self) -> None:
        pub = ConfigurableEventPublisher(mode="sync")
        pub.publish(_make_event("OrphanEvent"))  # no handler registered

    def test_handler_exception_does_not_stop_other_handlers(self) -> None:
        pub = ConfigurableEventPublisher(mode="sync")
        results: list[str] = []

        def bad_handler(e: DomainEvent) -> None:
            raise RuntimeError("handler broken")

        def good_handler(e: DomainEvent) -> None:
            results.append("ok")

        pub.register_handler("TestEvent", bad_handler)
        pub.register_handler("TestEvent", good_handler)
        pub.publish(_make_event("TestEvent"))
        assert results == ["ok"]

    def test_multiple_handlers_all_called(self) -> None:
        pub = ConfigurableEventPublisher(mode="sync")
        calls: list[int] = []

        pub.register_handler("Evt", lambda e: calls.append(1))
        pub.register_handler("Evt", lambda e: calls.append(2))
        pub.publish(_make_event("Evt"))
        assert sorted(calls) == [1, 2]


@pytest.mark.unit
class TestConfigurableEventPublisherPublishAsync:
    """Tests for async mode (future queue publishing)."""

    def test_async_mode_does_not_raise(self) -> None:
        pub = ConfigurableEventPublisher(mode="async")
        pub.publish(_make_event())

    def test_async_mode_logs_queue_message(self) -> None:
        pub = ConfigurableEventPublisher(mode="async")
        mock_logger = MagicMock()
        pub._logger = mock_logger
        pub.publish(_make_event("QueueEvent"))
        mock_logger.info.assert_called_once()


@pytest.mark.unit
class TestConfigurableEventPublisherHandlerRegistry:
    """Tests for register_handler and get_registered_handlers."""

    def test_get_registered_handlers_empty_initially(self) -> None:
        pub = ConfigurableEventPublisher(mode="sync")
        assert pub.get_registered_handlers() == {}

    def test_get_registered_handlers_counts_correctly(self) -> None:
        pub = ConfigurableEventPublisher(mode="sync")
        pub.register_handler("A", lambda e: None)
        pub.register_handler("A", lambda e: None)
        pub.register_handler("B", lambda e: None)
        counts = pub.get_registered_handlers()
        assert counts["A"] == 2
        assert counts["B"] == 1


@pytest.mark.unit
class TestCreateEventPublisherFactory:
    """Tests for the create_event_publisher factory function."""

    def test_factory_returns_publisher_with_correct_mode(self) -> None:
        pub = create_event_publisher(mode="sync")
        assert isinstance(pub, ConfigurableEventPublisher)
        assert pub.mode == "sync"

    def test_factory_default_mode_is_logging(self) -> None:
        pub = create_event_publisher()
        assert pub.mode == "logging"

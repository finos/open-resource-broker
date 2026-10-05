"""Unit tests for ActionEventHandler.

Covers the Template Method pattern: can_handle_event gating,
execute_action invocation, handle_action_result/handle_action_error
default hooks, and extract_action_data.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.events.base.action_event_handler import ActionEventHandler
from orb.domain.base.events.base_events import DomainEvent


def _event(**extra) -> DomainEvent:
    return DomainEvent(
        aggregate_id="agg-1",
        aggregate_type="TestAgg",
        event_type="TestEvent",
        **extra,
    )


@pytest.mark.unit
class TestActionEventHandlerProcessEvent:
    @pytest.mark.asyncio
    async def test_executes_action_when_can_handle_returns_true(self):
        calls = []

        class _Concrete(ActionEventHandler):
            async def execute_action(self, event):
                calls.append(event)
                return "action-result"

        h = _Concrete()
        await h.process_event(_event())

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_skips_action_when_can_handle_returns_false(self):
        calls = []
        logger = MagicMock()

        class _Concrete(ActionEventHandler):
            async def can_handle_event(self, event):
                return False

            async def execute_action(self, event):
                calls.append(event)
                return "action-result"

        h = _Concrete(logger=logger)
        await h.process_event(_event())

        assert calls == []
        logger.debug.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_action_result_default_logs_debug(self):
        logger = MagicMock()

        class _Concrete(ActionEventHandler):
            async def execute_action(self, event):
                return "result"

        h = _Concrete(logger=logger)
        await h.process_event(_event())

        logger.debug.assert_called_once()
        message = logger.debug.call_args[0][0]
        assert "Action completed" in message

    @pytest.mark.asyncio
    async def test_handle_action_result_can_be_overridden(self):
        results_seen = []

        class _Concrete(ActionEventHandler):
            async def execute_action(self, event):
                return "custom-result"

            async def handle_action_result(self, event, result):
                results_seen.append(result)

        h = _Concrete()
        await h.process_event(_event())

        assert results_seen == ["custom-result"]

    @pytest.mark.asyncio
    async def test_can_handle_event_default_is_true(self):
        class _Concrete(ActionEventHandler):
            async def execute_action(self, event):
                return None

        h = _Concrete()

        result = await h.can_handle_event(_event())
        assert result is True


@pytest.mark.unit
class TestActionEventHandlerErrorHandling:
    @pytest.mark.asyncio
    async def test_handle_action_error_default_logs_error(self):
        logger = MagicMock()

        class _Failing(ActionEventHandler):
            async def execute_action(self, event):
                raise RuntimeError("boom")

        h = _Failing(logger=logger)
        h.retry_count = 1
        h.retry_delay = 0.0

        with pytest.raises(RuntimeError, match="boom"):
            await h.handle(_event())

        # error logged via handle_action_error in addition to base error handling
        assert logger.error.called

    @pytest.mark.asyncio
    async def test_handle_action_error_can_be_overridden(self):
        errors_seen = []

        class _Failing(ActionEventHandler):
            async def execute_action(self, event):
                raise ValueError("custom failure")

            async def handle_action_error(self, event, error):
                errors_seen.append(str(error))

        h = _Failing()
        h.retry_count = 1
        h.retry_delay = 0.0

        with pytest.raises(ValueError):
            await h.handle(_event())

        assert errors_seen == ["custom failure"]


@pytest.mark.unit
class TestExtractActionData:
    def test_extracts_known_fields(self):
        class _Concrete(ActionEventHandler):
            async def execute_action(self, event):
                return None

        h = _Concrete()
        ev = _event()

        data = h.extract_action_data(ev)

        assert data["aggregate_id"] == "agg-1"
        assert data["aggregate_type"] == "TestAgg"
        assert "event_id" in data
        assert "occurred_at" in data

"""Unit tests for template event handlers (TemplateValidatedHandler, TemplateUpdatedHandler)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.events.handlers.template_handlers import (
    TemplateUpdatedHandler,
    TemplateValidatedHandler,
)
from orb.domain.base.events.base_events import DomainEvent


def _event() -> DomainEvent:
    return DomainEvent(
        aggregate_id="tmpl-1",
        aggregate_type="Template",
        event_type="TemplateEvent",
    )


@pytest.mark.unit
class TestTemplateValidatedHandler:
    def test_format_message_with_defaults(self):
        h = TemplateValidatedHandler()
        msg = h.format_message(_event())  # type: ignore[attr-defined]

        assert "unknown" in msg
        assert "Template validated" in msg

    def test_format_message_includes_template_name_and_status(self):
        h = TemplateValidatedHandler()
        ev = MagicMock()
        ev.template_name = "gpu-template"
        ev.validation_status = "passed"
        ev.validation_errors = []
        ev.validation_time = None

        msg = h.format_message(ev)  # type: ignore[attr-defined]

        assert "gpu-template" in msg
        assert "passed" in msg

    def test_format_message_includes_error_count_when_errors_present(self):
        h = TemplateValidatedHandler()
        ev = MagicMock()
        ev.template_name = "t1"
        ev.validation_status = "failed"
        ev.validation_errors = ["missing field a", "missing field b"]
        ev.validation_time = None

        msg = h.format_message(ev)  # type: ignore[attr-defined]

        assert "Errors: 2" in msg

    def test_format_message_includes_duration_when_present(self):
        h = TemplateValidatedHandler()
        ev = MagicMock()
        ev.template_name = "t1"
        ev.validation_status = "passed"
        ev.validation_errors = []
        ev.validation_time = 1500.0

        msg = h.format_message(ev)  # type: ignore[attr-defined]

        assert "Time:" in msg

    @pytest.mark.asyncio
    async def test_process_event_logs_formatted_message(self):
        logger = MagicMock()
        h = TemplateValidatedHandler(logger=logger)
        ev = MagicMock()
        ev.template_name = "t1"
        ev.validation_status = "passed"
        ev.validation_errors = []
        ev.validation_time = None

        await h.process_event(ev)

        logger.info.assert_called_once()
        assert "t1" in logger.info.call_args[0][0]


@pytest.mark.unit
class TestTemplateUpdatedHandler:
    def test_format_message_with_defaults(self):
        h = TemplateUpdatedHandler()
        msg = h.format_message(_event())  # type: ignore[attr-defined]

        assert "unknown" in msg
        assert "system" in msg

    def test_format_message_includes_change_count(self):
        h = TemplateUpdatedHandler()
        ev = MagicMock()
        ev.template_name = "t2"
        ev.changes = ["instance_type", "max_count"]
        ev.updated_by = "system"
        ev.version = None

        msg = h.format_message(ev)  # type: ignore[attr-defined]

        assert "Changes: 2" in msg

    def test_format_message_includes_version_when_present(self):
        h = TemplateUpdatedHandler()
        ev = MagicMock()
        ev.template_name = "t2"
        ev.changes = []
        ev.updated_by = "system"
        ev.version = 3

        msg = h.format_message(ev)  # type: ignore[attr-defined]

        assert "Version: 3" in msg

    def test_format_message_includes_updated_by(self):
        h = TemplateUpdatedHandler()
        ev = MagicMock()
        ev.template_name = "t2"
        ev.changes = []
        ev.updated_by = "alice"
        ev.version = None

        msg = h.format_message(ev)  # type: ignore[attr-defined]

        assert "t2" in msg
        assert "alice" in msg

    @pytest.mark.asyncio
    async def test_process_event_logs_formatted_message(self):
        logger = MagicMock()
        h = TemplateUpdatedHandler(logger=logger)
        ev = MagicMock()
        ev.template_name = "t2"
        ev.changes = []
        ev.updated_by = "alice"
        ev.version = None

        await h.process_event(ev)

        logger.info.assert_called_once()
        assert "alice" in logger.info.call_args[0][0]

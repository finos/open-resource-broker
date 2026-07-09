"""Unit tests for bulk query handlers (B5).

Tests cover GetMultipleRequestsHandler, GetMultipleTemplatesHandler,
GetMultipleMachinesHandler — each returns all requested entities via the
handler and reports not-found IDs correctly.

Bulk handlers lazy-import services and factories inside __init__.  To avoid
coupling tests to those transitive dependencies, handlers are constructed via
object.__new__ and their internal collaborators are injected directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.bulk_queries import (
    GetMultipleMachinesQuery,
    GetMultipleRequestsQuery,
    GetMultipleTemplatesQuery,
)
from orb.application.queries.bulk_handlers import (
    GetMultipleMachinesHandler,
    GetMultipleRequestsHandler,
    GetMultipleTemplatesHandler,
)
from orb.domain.base.exceptions import EntityNotFoundError

# ---------------------------------------------------------------------------
# Construction helpers — bypass __init__ to avoid lazy-import dependencies
# ---------------------------------------------------------------------------


def _make_requests_handler() -> GetMultipleRequestsHandler:
    handler = object.__new__(GetMultipleRequestsHandler)
    handler.logger = MagicMock()
    handler.error_handler = MagicMock()
    handler.uow_factory = MagicMock()
    handler._container = MagicMock()
    handler._query_service = MagicMock()
    handler._dto_factory = MagicMock()
    return handler


def _make_templates_handler() -> GetMultipleTemplatesHandler:
    handler = object.__new__(GetMultipleTemplatesHandler)
    handler.logger = MagicMock()
    handler.error_handler = MagicMock()
    handler.uow_factory = MagicMock()
    handler._container = MagicMock()
    handler._query_service = MagicMock()
    handler._dto_factory = MagicMock()
    return handler


def _make_machines_handler() -> GetMultipleMachinesHandler:
    handler = object.__new__(GetMultipleMachinesHandler)
    handler.logger = MagicMock()
    handler.error_handler = MagicMock()
    handler.uow_factory = MagicMock()
    handler._container = MagicMock()
    handler._query_service = MagicMock()
    handler._dto_factory = MagicMock()
    return handler


def _make_request_dto(request_id: str = "req-a"):
    from datetime import datetime, timezone

    from orb.application.dto.responses import RequestDTO

    return RequestDTO.model_validate(
        {
            "request_id": request_id,
            "status": "complete",
            "requested_count": 1,
            "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }
    )


# ---------------------------------------------------------------------------
# GetMultipleRequestsHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetMultipleRequestsHandler:
    async def test_all_found_returns_full_batch(self):
        handler = _make_requests_handler()
        dto_a = _make_request_dto("req-a")
        dto_b = _make_request_dto("req-b")
        handler._query_service.get_request = AsyncMock(side_effect=[MagicMock(), MagicMock()])
        handler._query_service.get_machines_for_request = AsyncMock(return_value=[])
        handler._dto_factory.create_from_domain.side_effect = [dto_a, dto_b]

        result = await handler.execute_query(
            GetMultipleRequestsQuery(request_ids=["req-a", "req-b"], include_machines=False)
        )

        assert result.found_count == 2
        assert result.total_requested == 2
        assert result.not_found_ids == []
        assert len(result.requests) == 2

    async def test_partial_found_reports_not_found_ids(self):
        handler = _make_requests_handler()
        dto_a = _make_request_dto("req-a")
        handler._query_service.get_request = AsyncMock(
            side_effect=[MagicMock(), EntityNotFoundError("Request", "req-missing")]
        )
        handler._query_service.get_machines_for_request = AsyncMock(return_value=[])
        handler._dto_factory.create_from_domain.return_value = dto_a

        result = await handler.execute_query(
            GetMultipleRequestsQuery(request_ids=["req-a", "req-missing"])
        )

        assert result.found_count == 1
        assert result.total_requested == 2
        assert "req-missing" in result.not_found_ids

    async def test_empty_id_list_returns_empty_batch(self):
        handler = _make_requests_handler()
        handler._query_service.get_request = AsyncMock()

        result = await handler.execute_query(GetMultipleRequestsQuery(request_ids=[]))

        assert result.found_count == 0
        assert result.total_requested == 0
        assert result.not_found_ids == []
        handler._query_service.get_request.assert_not_called()

    async def test_all_not_found_reports_all_ids(self):
        handler = _make_requests_handler()
        handler._query_service.get_request = AsyncMock(
            side_effect=EntityNotFoundError("Request", "req-x")
        )

        result = await handler.execute_query(
            GetMultipleRequestsQuery(request_ids=["req-x", "req-y"])
        )

        assert result.found_count == 0
        assert set(result.not_found_ids) == {"req-x", "req-y"}
        assert result.total_requested == 2


# ---------------------------------------------------------------------------
# GetMultipleTemplatesHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetMultipleTemplatesHandler:
    async def test_all_active_templates_returned(self):
        handler = _make_templates_handler()
        fake_dto_a = MagicMock()
        fake_dto_b = MagicMock()
        handler._query_service.get_template = AsyncMock(
            side_effect=[MagicMock(active=True), MagicMock(active=True)]
        )
        handler._dto_factory.create_from_domain.side_effect = [fake_dto_a, fake_dto_b]

        result = await handler.execute_query(
            GetMultipleTemplatesQuery(template_ids=["tmpl-a", "tmpl-b"], active_only=True)
        )

        assert result.found_count == 2
        assert result.not_found_ids == []
        assert result.total_requested == 2

    async def test_inactive_template_excluded_when_active_only(self):
        handler = _make_templates_handler()
        handler._query_service.get_template = AsyncMock(return_value=MagicMock(active=False))

        result = await handler.execute_query(
            GetMultipleTemplatesQuery(template_ids=["tmpl-inactive"], active_only=True)
        )

        assert result.found_count == 0
        assert "tmpl-inactive" in result.not_found_ids

    async def test_inactive_template_included_when_not_active_only(self):
        handler = _make_templates_handler()
        handler._query_service.get_template = AsyncMock(return_value=MagicMock(active=False))
        handler._dto_factory.create_from_domain.return_value = MagicMock()

        result = await handler.execute_query(
            GetMultipleTemplatesQuery(template_ids=["tmpl-inactive"], active_only=False)
        )

        assert result.found_count == 1
        assert result.not_found_ids == []

    async def test_not_found_template_reported(self):
        handler = _make_templates_handler()
        handler._query_service.get_template = AsyncMock(
            side_effect=EntityNotFoundError("Template", "tmpl-gone")
        )

        result = await handler.execute_query(GetMultipleTemplatesQuery(template_ids=["tmpl-gone"]))

        assert result.found_count == 0
        assert "tmpl-gone" in result.not_found_ids


# ---------------------------------------------------------------------------
# GetMultipleMachinesHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetMultipleMachinesHandler:
    async def test_all_found_returns_full_batch(self):
        from orb.application.machine.dto import MachineDTO

        handler = _make_machines_handler()
        _mc_fields = {
            "name": "i-test",
            "status": "running",
            "instance_type": "t3.small",
            "private_ip": "10.0.0.1",
            "result": "executing",
        }
        dto_a = MachineDTO.model_validate({"machine_id": "mc-a", **_mc_fields})
        dto_b = MachineDTO.model_validate({"machine_id": "mc-b", **_mc_fields})

        handler._query_service.get_machine = AsyncMock(
            side_effect=[
                MagicMock(machine_id="mc-a", request_id=None),
                MagicMock(machine_id="mc-b", request_id=None),
            ]
        )
        handler._dto_factory.create_from_domain.side_effect = [dto_a, dto_b]

        result = await handler.execute_query(
            GetMultipleMachinesQuery(machine_ids=["mc-a", "mc-b"], include_requests=False)
        )

        assert result.found_count == 2
        assert result.not_found_ids == []
        assert result.total_requested == 2

    async def test_not_found_machine_reported(self):
        handler = _make_machines_handler()
        handler._query_service.get_machine = AsyncMock(
            side_effect=EntityNotFoundError("Machine", "mc-gone")
        )

        result = await handler.execute_query(GetMultipleMachinesQuery(machine_ids=["mc-gone"]))

        assert result.found_count == 0
        assert "mc-gone" in result.not_found_ids
        assert result.total_requested == 1

    async def test_empty_id_list_returns_empty_batch(self):
        handler = _make_machines_handler()
        handler._query_service.get_machine = AsyncMock()

        result = await handler.execute_query(GetMultipleMachinesQuery(machine_ids=[]))

        assert result.found_count == 0
        assert result.total_requested == 0
        handler._query_service.get_machine.assert_not_called()

    async def test_partial_found_reports_not_found(self):
        from orb.application.machine.dto import MachineDTO

        handler = _make_machines_handler()
        _mc_fields = {
            "name": "i-test",
            "status": "running",
            "instance_type": "t3.small",
            "private_ip": "10.0.0.1",
            "result": "executing",
        }
        dto_a = MachineDTO.model_validate({"machine_id": "mc-a", **_mc_fields})

        handler._query_service.get_machine = AsyncMock(
            side_effect=[
                MagicMock(machine_id="mc-a", request_id=None),
                EntityNotFoundError("Machine", "mc-gone"),
            ]
        )
        handler._dto_factory.create_from_domain.return_value = dto_a

        result = await handler.execute_query(
            GetMultipleMachinesQuery(machine_ids=["mc-a", "mc-gone"])
        )

        assert result.found_count == 1
        assert "mc-gone" in result.not_found_ids
        assert result.total_requested == 2

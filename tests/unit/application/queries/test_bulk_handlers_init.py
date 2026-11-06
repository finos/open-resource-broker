"""Unit tests for bulk query handler construction and delegation.

Complements test_bulk_handlers.py (which bypasses __init__ via object.__new__)
by exercising the real __init__ wiring and query-bus delegation of the bulk
handlers in application/queries/bulk_handlers.py. Constructing the handlers
through their public constructors here guards against a regression where a
handler's __init__ referenced a module that does not exist, which the
object.__new__ tests could never catch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_machine_dto(machine_id: str = "mc-a"):
    from orb.application.machine.dto import MachineDTO

    return MachineDTO.model_validate(
        {
            "machine_id": machine_id,
            "name": "i-test",
            "status": "running",
            "instance_type": "t3.small",
            "private_ip": "10.0.0.1",
            "result": "executing",
        }
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetMultipleRequestsInit:
    async def test_init_wires_services_and_include_machines(self):
        with (
            patch("orb.application.services.request_query_service.RequestQueryService") as MockQS,
            patch("orb.application.factories.request_dto_factory.RequestDTOFactory") as MockFactory,
        ):
            qs = MockQS.return_value
            qs.get_request = AsyncMock(return_value=MagicMock())
            qs.get_machines_for_request = AsyncMock(return_value=["m1"])
            MockFactory.return_value.create_from_domain.return_value = _make_request_dto()

            handler = GetMultipleRequestsHandler(
                uow_factory=MagicMock(),
                logger=MagicMock(),
                error_handler=MagicMock(),
            )

            result = await handler.execute_query(
                GetMultipleRequestsQuery(request_ids=["req-a"], include_machines=True)
            )

        qs.get_machines_for_request.assert_awaited_once()
        assert result.found_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetMultipleMachinesInit:
    """Exercise the real __init__ wiring and query-bus delegation.

    These construct the handler through its public constructor (no
    object.__new__ bypass) so a broken import or a missing collaborator in
    __init__ surfaces here rather than being silently skipped.
    """

    def _handler(self, query_bus: MagicMock) -> GetMultipleMachinesHandler:
        return GetMultipleMachinesHandler(
            uow_factory=MagicMock(),
            logger=MagicMock(),
            error_handler=MagicMock(),
            query_bus=query_bus,
        )

    async def test_delegates_to_single_machine_query(self):
        query_bus = MagicMock()
        query_bus.execute = AsyncMock(return_value=_make_machine_dto())
        handler = self._handler(query_bus)

        result = await handler.execute_query(
            GetMultipleMachinesQuery(machine_ids=["mc-a"], include_requests=True)
        )

        query_bus.execute.assert_awaited_once()
        assert result.found_count == 1

    async def test_not_found_reported(self):
        from orb.domain.base.exceptions import EntityNotFoundError

        query_bus = MagicMock()
        query_bus.execute = AsyncMock(side_effect=EntityNotFoundError("Machine", "mc-gone"))
        handler = self._handler(query_bus)

        result = await handler.execute_query(GetMultipleMachinesQuery(machine_ids=["mc-gone"]))

        assert result.found_count == 0
        assert "mc-gone" in result.not_found_ids


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetMultipleTemplatesInit:
    """Exercise the real __init__ wiring and query-bus delegation for templates.

    Constructing through the public constructor and executing the query proves
    the handler no longer references a non-existent template query service.
    """

    def _handler(self, query_bus: MagicMock) -> GetMultipleTemplatesHandler:
        return GetMultipleTemplatesHandler(
            uow_factory=MagicMock(),
            logger=MagicMock(),
            error_handler=MagicMock(),
            query_bus=query_bus,
        )

    async def test_delegates_to_single_template_query(self):
        query_bus = MagicMock()
        query_bus.execute = AsyncMock(return_value=MagicMock(is_active=True))
        handler = self._handler(query_bus)

        result = await handler.execute_query(
            GetMultipleTemplatesQuery(template_ids=["tmpl-a"], active_only=True)
        )

        query_bus.execute.assert_awaited_once()
        assert result.found_count == 1
        assert result.not_found_ids == []

    async def test_not_found_reported(self):
        from orb.domain.base.exceptions import EntityNotFoundError

        query_bus = MagicMock()
        query_bus.execute = AsyncMock(side_effect=EntityNotFoundError("Template", "tmpl-gone"))
        handler = self._handler(query_bus)

        result = await handler.execute_query(GetMultipleTemplatesQuery(template_ids=["tmpl-gone"]))

        assert result.found_count == 0
        assert "tmpl-gone" in result.not_found_ids

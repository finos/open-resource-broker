"""Unit tests for SyncRequestHandler and PopulateMachineIdsHandler edge paths.

Covers application/commands/request_sync_handlers.py branches not exercised by
the dispatch tests: the sync happy path, not-found handling, provider-error
propagation, and the machine-id discovery error branch. Only abstract ports and
application services are mocked.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.commands.request_sync_handlers import (
    PopulateMachineIdsHandler,
    SyncRequestHandler,
)
from orb.application.dto.commands import PopulateMachineIdsCommand, SyncRequestCommand
from orb.domain.base.exceptions import EntityNotFoundError

_VALID_REQUEST_ID = "req-00000000-0000-0000-0000-000000000001"


def _make_uow_factory(request) -> MagicMock:
    uow = MagicMock()
    uow.requests.get_by_id.return_value = request
    uow.requests.save = MagicMock()

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = _create
    return factory


def _ports() -> dict:
    return {
        "logger": MagicMock(),
        "event_publisher": MagicMock(),
        "error_handler": MagicMock(),
    }


# ---------------------------------------------------------------------------
# SyncRequestHandler
# ---------------------------------------------------------------------------


class TestSyncRequestHandler:
    def _handler(
        self, request, machine_sync_service: MagicMock, status_service: MagicMock
    ) -> SyncRequestHandler:
        return SyncRequestHandler(
            uow_factory=_make_uow_factory(request),
            machine_sync_service=machine_sync_service,
            status_service=status_service,
            **_ports(),
        )

    @pytest.mark.asyncio
    async def test_syncs_and_updates_status(self):
        request = MagicMock()
        machine_sync_service = MagicMock()
        machine_sync_service.fetch_provider_machines = AsyncMock(return_value=([], {}))
        machine_sync_service.sync_machines_with_provider = AsyncMock(return_value=([], []))
        status_service = MagicMock()
        status_service.determine_status_from_machines.return_value = ("completed", "done")
        status_service.update_request_status = AsyncMock()

        handler = self._handler(request, machine_sync_service, status_service)

        with patch("orb.application.services.request_query_service.RequestQueryService") as MockQS:
            qs = MockQS.return_value
            qs.get_machines_for_request = AsyncMock(return_value=[])
            await handler.handle(SyncRequestCommand(request_id=_VALID_REQUEST_ID))

        status_service.update_request_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_status_update_when_status_unchanged(self):
        request = MagicMock()
        machine_sync_service = MagicMock()
        machine_sync_service.fetch_provider_machines = AsyncMock(return_value=([], {}))
        machine_sync_service.sync_machines_with_provider = AsyncMock(return_value=([], []))
        status_service = MagicMock()
        status_service.determine_status_from_machines.return_value = (None, None)
        status_service.update_request_status = AsyncMock()

        handler = self._handler(request, machine_sync_service, status_service)

        with patch("orb.application.services.request_query_service.RequestQueryService") as MockQS:
            qs = MockQS.return_value
            qs.get_machines_for_request = AsyncMock(return_value=[])
            await handler.handle(SyncRequestCommand(request_id=_VALID_REQUEST_ID))

        status_service.update_request_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_found_raises_entity_not_found(self):
        handler = self._handler(None, MagicMock(), MagicMock())

        with pytest.raises(EntityNotFoundError):
            await handler.handle(SyncRequestCommand(request_id=_VALID_REQUEST_ID))

    @pytest.mark.asyncio
    async def test_provider_error_propagates(self):
        request = MagicMock()
        machine_sync_service = MagicMock()
        machine_sync_service.fetch_provider_machines = AsyncMock(
            side_effect=RuntimeError("provider down")
        )
        status_service = MagicMock()

        handler = self._handler(request, machine_sync_service, status_service)

        with patch("orb.application.services.request_query_service.RequestQueryService") as MockQS:
            qs = MockQS.return_value
            qs.get_machines_for_request = AsyncMock(return_value=[])
            with pytest.raises(RuntimeError):
                await handler.handle(SyncRequestCommand(request_id=_VALID_REQUEST_ID))


# ---------------------------------------------------------------------------
# PopulateMachineIdsHandler edge paths
# ---------------------------------------------------------------------------


class TestPopulateMachineIdsEdges:
    def _handler(self, request, provider_selection_port) -> PopulateMachineIdsHandler:
        return PopulateMachineIdsHandler(
            uow_factory=_make_uow_factory(request),
            provider_selection_port=provider_selection_port,
            **_ports(),
        )

    @pytest.mark.asyncio
    async def test_skips_when_request_missing(self):
        provider_selection_port = MagicMock()
        provider_selection_port.execute_operation = AsyncMock()
        handler = self._handler(None, provider_selection_port)

        await handler.handle(PopulateMachineIdsCommand(request_id=_VALID_REQUEST_ID))

        provider_selection_port.execute_operation.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_population_not_needed(self):
        request = MagicMock()
        request.needs_machine_id_population.return_value = False
        provider_selection_port = MagicMock()
        provider_selection_port.execute_operation = AsyncMock()
        handler = self._handler(request, provider_selection_port)

        await handler.handle(PopulateMachineIdsCommand(request_id=_VALID_REQUEST_ID))

        provider_selection_port.execute_operation.assert_not_called()

    @pytest.mark.asyncio
    async def test_discovery_error_is_swallowed(self):
        request = MagicMock()
        request.needs_machine_id_population.return_value = True
        request.resource_ids = ["fleet-1"]
        request.provider_api = "SpotFleet"
        request.template_id = "tpl-1"
        request.provider_name = "aws"

        async def _boom(provider_name, operation):
            raise RuntimeError("describe failed")

        provider_selection_port = MagicMock()
        provider_selection_port.execute_operation = _boom
        handler = self._handler(request, provider_selection_port)

        # Must not raise; discovery failure returns an empty id list.
        await handler.handle(PopulateMachineIdsCommand(request_id=_VALID_REQUEST_ID))

        request.update_machine_ids.assert_not_called()

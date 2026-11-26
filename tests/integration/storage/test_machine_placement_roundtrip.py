# ruff: noqa: F811

"""Placement data on machines survives storage and reaches the request DTO."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.dto.queries import SyncAndGetRequestQuery
from orb.application.queries.request_query_handlers import SyncAndGetRequestHandler
from orb.application.services.request_status_management_service import (
    RequestStatusManagementService,
)
from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine
from orb.domain.request.request_types import RequestStatus
from tests.integration.storage.test_handler_conflict_retry import (  # noqa: F401  (fixture)
    _new_request,
    make_uow,
)


class _Factory:
    def __init__(self, make_uow) -> None:
        self._make_uow = make_uow

    def create_unit_of_work(self):
        return self._make_uow()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stored_provider_data_reaches_request_dto(make_uow) -> None:
    request = _new_request().model_copy(update={"status": RequestStatus.COMPLETED})
    service = RequestStatusManagementService(uow_factory=MagicMock(), logger=MagicMock())
    machine = service._create_machine_aggregate(
        {
            "instance_id": "i-0abc123",
            "instance_type": "t3.large",
            "provider_data": {"availability_zone": "eu-west-1a", "vcpus": 2},
        },
        request,
        request.template_id,
    )
    with make_uow() as uow:
        uow.requests.save(request)
        uow.machines.save(machine)

    handler = SyncAndGetRequestHandler(
        uow_factory=_Factory(make_uow),  # type: ignore[arg-type]
        logger=MagicMock(),
        error_handler=MagicMock(),
        provider_registry_service=MagicMock(),
        machine_sync_service=MagicMock(),
        cache_service=MagicMock(is_caching_enabled=MagicMock(return_value=False)),
        event_publisher=MagicMock(),
        state_machine=FulfilmentStateMachine(grace_period_seconds=60),
    )

    dto = await handler.execute_query(SyncAndGetRequestQuery(request_id=str(request.request_id)))

    assert len(dto.machine_references) == 1
    ref = dto.machine_references[0]
    assert ref.availability_zone == "eu-west-1a"
    assert ref.vcpus == 2

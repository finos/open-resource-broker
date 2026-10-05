"""Unit tests for RequestQueryService.

Covers:
- get_request: found and not-found paths.
- get_machines_for_request: ACQUIRE vs RETURN routing, and error swallowing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.services.request_query_service import RequestQueryService
from orb.domain.request.exceptions import RequestNotFoundError
from orb.domain.request.request_types import RequestType

REQUEST_ID = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def _make_uow_factory(request_result=None, machines_result=None, raise_on_find=None) -> MagicMock:
    repo = MagicMock()
    repo.get_by_id.return_value = request_result

    machines_repo = MagicMock()
    if raise_on_find is not None:
        machines_repo.find_by_request_id.side_effect = raise_on_find
        machines_repo.find_by_return_request_id.side_effect = raise_on_find
    else:
        machines_repo.find_by_request_id.return_value = machines_result or []
        machines_repo.find_by_return_request_id.return_value = machines_result or []

    uow = MagicMock()
    uow.requests = repo
    uow.machines = machines_repo
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)

    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    return factory


@pytest.mark.unit
class TestGetRequest:
    @pytest.mark.asyncio
    async def test_returns_request_when_found(self):
        request = MagicMock()
        factory = _make_uow_factory(request_result=request)
        service = RequestQueryService(uow_factory=factory, logger=MagicMock())

        result = await service.get_request(REQUEST_ID)

        assert result is request

    @pytest.mark.asyncio
    async def test_raises_not_found_when_missing(self):
        factory = _make_uow_factory(request_result=None)
        service = RequestQueryService(uow_factory=factory, logger=MagicMock())

        with pytest.raises(RequestNotFoundError):
            await service.get_request(REQUEST_ID)


@pytest.mark.unit
class TestGetMachinesForRequest:
    @pytest.mark.asyncio
    async def test_acquire_request_uses_find_by_request_id(self):
        machines = [MagicMock(), MagicMock()]
        factory = _make_uow_factory(machines_result=machines)
        service = RequestQueryService(uow_factory=factory, logger=MagicMock())

        request = MagicMock()
        request.request_type = RequestType.ACQUIRE
        request.request_id.value = REQUEST_ID

        result = await service.get_machines_for_request(request)

        assert result == machines

    @pytest.mark.asyncio
    async def test_return_request_uses_find_by_return_request_id(self):
        machines = [MagicMock()]
        factory = _make_uow_factory(machines_result=machines)
        service = RequestQueryService(uow_factory=factory, logger=MagicMock())

        request = MagicMock()
        request.request_type = RequestType.RETURN
        request.request_id.value = REQUEST_ID

        result = await service.get_machines_for_request(request)

        assert result == machines

    @pytest.mark.asyncio
    async def test_exception_is_swallowed_and_returns_empty_list(self):
        factory = _make_uow_factory(raise_on_find=RuntimeError("db down"))
        logger = MagicMock()
        service = RequestQueryService(uow_factory=factory, logger=logger)

        request = MagicMock()
        request.request_type = RequestType.ACQUIRE
        request.request_id.value = REQUEST_ID

        result = await service.get_machines_for_request(request)

        assert result == []
        logger.error.assert_called_once()

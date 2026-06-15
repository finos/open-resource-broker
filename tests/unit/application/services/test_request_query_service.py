from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.services.request_query_service import RequestQueryService
from orb.domain.request.request_types import RequestType


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_return_request_prefers_request_machine_ids_over_return_request_link():
    uow = MagicMock()
    uow.__enter__.return_value = uow
    uow.__exit__.return_value = None

    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.return_value = uow

    logger = MagicMock()
    service = RequestQueryService(uow_factory, logger)

    request = MagicMock()
    request.request_type = RequestType.RETURN
    request.request_id.value = "ret-123"
    request.machine_ids = ["machine-1", "machine-2"]

    expected = [MagicMock(), MagicMock()]
    uow.machines.find_by_ids.return_value = expected

    result = await service.get_machines_for_request(request)

    assert result == expected
    uow.machines.find_by_ids.assert_called_once_with(["machine-1", "machine-2"])
    uow.machines.find_by_return_request_id.assert_not_called()

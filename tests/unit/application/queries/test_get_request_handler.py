"""Tests for GetRequestHandler — sync fallback behaviour."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from orb.application.dto.queries import GetRequestQuery
from orb.application.dto.responses import RequestDTO
from orb.application.queries.request_query_handlers import GetRequestHandler
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports.container_port import ContainerPort
from orb.domain.base.ports.error_handling_port import ErrorHandlingPort
from orb.domain.request.aggregate import Request
from orb.domain.request.exceptions import RequestNotFoundError
from orb.domain.request.value_objects import RequestId, RequestStatus, RequestType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ID_FALLBACK = "req-00000000-0000-0000-0000-000000000001"
_ID_SUCCESS = "req-00000000-0000-0000-0000-000000000002"
_ID_MISSING = "req-00000000-0000-0000-0000-000000000003"


def _make_request(request_id: str = "req-00000000-0000-0000-0000-000000000004") -> Request:
    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        template_id="tmpl-1",
        requested_count=1,
        status=RequestStatus.PENDING,
    )


class _FakeContainer(ContainerPort):
    def __init__(self, services: dict | None = None) -> None:
        self._services: dict = services or {}

    def get(self, service_type):
        if service_type not in self._services:
            raise KeyError(service_type)
        return self._services[service_type]

    def register(self, service_type, instance) -> None:
        self._services[service_type] = instance

    def register_factory(self, service_type, factory_func) -> None:
        self._services[service_type] = factory_func()

    def register_singleton(self, service_type, factory_func) -> None:
        self._services[service_type] = factory_func()

    def has(self, service_type) -> bool:
        return service_type in self._services


def _make_handler(
    request: Request,
    sync_side_effect=None,
) -> tuple[GetRequestHandler, MagicMock, MagicMock]:
    """Build a GetRequestHandler with all collaborators mocked.

    Returns (handler, mock_query_service, mock_cache_service).
    """
    logger = Mock()
    error_handler = Mock(spec=ErrorHandlingPort)
    container = _FakeContainer()  # empty — cache/event publisher will fall back to None/noop

    # UoW factory — used by record_status_check save; configure context manager support
    uow_factory = Mock()
    mock_uow = MagicMock()
    mock_uow.requests = Mock()
    mock_uow.requests.save = Mock()
    uow_factory.create_unit_of_work = Mock(return_value=mock_uow)
    mock_uow.__enter__ = Mock(return_value=mock_uow)
    mock_uow.__exit__ = Mock(return_value=False)

    # Provider registry — not exercised in these tests
    provider_registry_service = Mock()

    # Machine sync service
    machine_sync_service = Mock()
    machine_sync_service.populate_missing_machine_ids = AsyncMock(side_effect=sync_side_effect)
    machine_sync_service.fetch_provider_machines = AsyncMock(return_value=([], {}))
    machine_sync_service.sync_machines_with_provider = AsyncMock(return_value=([], []))

    handler = GetRequestHandler(
        uow_factory=uow_factory,
        logger=logger,
        error_handler=error_handler,
        container=container,
        provider_registry_service=provider_registry_service,
        machine_sync_service=machine_sync_service,
    )

    # Replace internal services with mocks so we control data flow
    mock_query_service = AsyncMock()
    mock_query_service.get_request = AsyncMock(return_value=request)
    mock_query_service.get_machines_for_request = AsyncMock(return_value=[])
    handler._query_service = mock_query_service

    # Mock status service so it doesn't try to use the real uow
    mock_status_service = Mock()
    mock_status_service.determine_status_from_machines = Mock(return_value=(None, None))
    mock_status_service.update_request_status = AsyncMock(return_value=request)
    handler._status_service = mock_status_service

    mock_cache_service = Mock()
    mock_cache_service.is_caching_enabled = Mock(return_value=True)
    mock_cache_service.get_cached_request = Mock(return_value=None)  # no cache hit
    mock_cache_service.cache_request = Mock()
    handler._cache_service = mock_cache_service

    return handler, mock_query_service, mock_cache_service


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_request_falls_back_to_stored_state_on_sync_error():
    """When sync raises, handler returns a DTO from the pre-sync request and does NOT write cache."""
    request = _make_request(_ID_FALLBACK)
    sync_error = RuntimeError("provider unreachable")

    handler, _mock_query_service, mock_cache_service = _make_handler(
        request, sync_side_effect=sync_error
    )

    query = GetRequestQuery(request_id=_ID_FALLBACK)
    result = await handler.execute_query(query)

    # Must return a valid DTO, not raise
    assert isinstance(result, RequestDTO)
    assert result.request_id == _ID_FALLBACK

    # Cache must NOT be written on the fallback path (stale data)
    mock_cache_service.cache_request.assert_not_called()


@pytest.mark.asyncio
async def test_get_request_returns_synced_dto_on_success():
    """Normal (no-error) path returns a DTO and writes to cache."""
    request = _make_request(_ID_SUCCESS)

    handler, mock_query_service, mock_cache_service = _make_handler(request, sync_side_effect=None)

    # After sync the handler re-fetches the request; return the same object for simplicity
    mock_query_service.get_request = AsyncMock(return_value=request)

    query = GetRequestQuery(request_id=_ID_SUCCESS)
    result = await handler.execute_query(query)

    assert isinstance(result, RequestDTO)
    assert result.request_id == _ID_SUCCESS

    # Cache SHOULD be written on the success path
    mock_cache_service.cache_request.assert_called_once()


@pytest.mark.asyncio
async def test_get_request_raises_entity_not_found_when_missing():
    """EntityNotFoundError (request missing) propagates — no swallowing."""
    request = _make_request(_ID_MISSING)

    handler, mock_query_service, _ = _make_handler(request, sync_side_effect=None)
    mock_query_service.get_request = AsyncMock(side_effect=RequestNotFoundError(_ID_MISSING))

    query = GetRequestQuery(request_id=_ID_MISSING)
    with pytest.raises(EntityNotFoundError):
        await handler.execute_query(query)

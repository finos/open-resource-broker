"""A provider sync must not put back an older copy of a request.

The request is loaded before the provider call, which can take a long time.
If the request is cancelled (or otherwise changed) meanwhile, the stamp the
sync writes afterwards must leave the stored request alone. Exercised against
real JSON and SQL storage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from orb.application.services.machine_sync_service import FailedProviderFetch, MachineSyncService
from orb.domain.request.aggregate import Request
from orb.domain.request.request_identifiers import RequestId
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType

_REQUEST_ID = "req-00000000-0000-0000-0000-0000000000aa"


class _Uow:
    """A unit of work exposing only the request repository, over real storage."""

    def __init__(self, repository) -> None:
        self.requests = repository

    def __enter__(self) -> "_Uow":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class _Factory:
    def __init__(self, uow: _Uow) -> None:
        self._uow = uow

    def create_unit_of_work(self) -> _Uow:
        return self._uow


@pytest.fixture(params=["json", "sql"])
def uow(request, tmp_path) -> _Uow:
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy
    from orb.infrastructure.storage.repositories.request_repository import RequestRepositoryImpl
    from orb.infrastructure.storage.sql.models import RequestModel
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

    if request.param == "json":
        strategy = JSONStorageStrategy(str(tmp_path / "requests.json"), entity_type="requests")
    else:
        columns = {
            col.key: ("TEXT PRIMARY KEY" if col.primary_key else "TEXT")
            for col in RequestModel.__table__.columns  # type: ignore[attr-defined]
        }
        strategy = SQLStorageStrategy(
            config={"type": "sqlite", "name": str(tmp_path / "requests.db")},
            table_name="requests",
            columns=columns,
        )
    return _Uow(RequestRepositoryImpl(strategy))


def _service(uow: _Uow) -> MachineSyncService:
    return MachineSyncService(
        command_bus=MagicMock(),
        uow_factory=_Factory(uow),  # type: ignore[arg-type]
        config_port=MagicMock(),
        logger=MagicMock(),
        provider_registry_service=None,
    )


def _stored(uow: _Uow) -> Request:
    with uow as u:
        stored = u.requests.get_by_id(RequestId(value=_REQUEST_ID))
    assert stored is not None
    return stored


@pytest.fixture
def in_flight(uow) -> Request:
    request = Request(
        request_id=RequestId(value=_REQUEST_ID),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        template_id="tmpl",
        requested_count=1,
        status=RequestStatus.IN_PROGRESS,
    )
    with uow as u:
        u.requests.save(request)
    return _stored(uow)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_stamps_an_unchanged_request(uow, in_flight: Request) -> None:
    await _service(uow).sync_machines_with_provider(in_flight, [], [])

    stored = _stored(uow)
    assert stored.last_status_check is not None
    assert stored.status == RequestStatus.IN_PROGRESS


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cancel_during_the_provider_call_is_not_overwritten(uow, in_flight: Request) -> None:
    stale = in_flight  # what the caller loaded before the provider call
    cancelled = stale.update_status(RequestStatus.CANCELLED, "cancelled by user", force=True)
    with uow as u:
        u.requests.save(cancelled)

    await _service(uow).sync_machines_with_provider(stale, [], [])

    stored = _stored(uow)
    assert stored.status == RequestStatus.CANCELLED
    assert stored.last_status_check is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_request_changed_during_the_provider_call_is_kept_and_stamped(
    uow, in_flight: Request
) -> None:
    stale = in_flight
    changed = stale.update_metadata({"note": "changed meanwhile"})
    with uow as u:
        u.requests.save(changed)
    after_change = _stored(uow)
    assert after_change.version != stale.version

    await _service(uow).sync_machines_with_provider(stale, [], [])

    # The change is kept, and the stamp is applied to the fresh copy.
    stored = _stored(uow)
    assert stored.metadata.get("note") == "changed meanwhile"
    assert stored.last_status_check is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_provider_fetch_does_not_refresh_freshness(uow, in_flight: Request) -> None:
    await _service(uow).sync_machines_with_provider(
        in_flight, [], FailedProviderFetch(), now=datetime.now(timezone.utc)
    )

    assert _stored(uow).last_status_check is None

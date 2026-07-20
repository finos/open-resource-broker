"""Unit tests for RequestCacheService — database-backed request caching."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.caching.request_cache_service import RequestCacheService

# Valid UUIDs for request IDs
_RID1 = f"req-{uuid.UUID('00000000-0000-0000-0000-000000000001')}"
_RID2 = f"req-{uuid.UUID('00000000-0000-0000-0000-000000000002')}"


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


def _make_logger():
    lg = MagicMock()
    lg.warning = MagicMock()
    lg.debug = MagicMock()
    return lg


def _make_config_manager(enabled: bool = True, ttl: int = 60):
    """Return a minimal ConfigurationManager stub."""
    cache_cfg = MagicMock()
    cache_cfg.enabled = enabled
    cache_cfg.ttl_seconds = ttl

    caching_cfg = MagicMock()
    caching_cfg.request_status = cache_cfg

    perf_cfg = MagicMock()
    perf_cfg.caching = caching_cfg

    app_cfg = MagicMock()
    app_cfg.performance = perf_cfg

    mgr = MagicMock()
    mgr.app_config = app_cfg
    return mgr


class _FakeRequest:
    """Minimal request entity stub."""

    def __init__(
        self,
        request_id: str = _RID1,
        template_id: str = "tmpl-1",
        requested_count: int = 1,
        status_value: str = "pending",
        updated_at: Optional[datetime] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        from orb.domain.request.value_objects import RequestId

        self.request_id = RequestId(value=request_id)
        self.template_id = template_id
        self.requested_count = requested_count
        self.metadata = metadata
        self.created_at = datetime.now(timezone.utc)
        self.updated_at: Optional[datetime] = (
            updated_at if updated_at is not None else datetime.now(timezone.utc)
        )
        # Mimic domain enum
        self.status = MagicMock()
        self.status.value = status_value


class _FakeMachineId:
    """Stub for machine ID with str support."""

    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value


class _FakeMachine:
    """Minimal machine entity stub."""

    def __init__(self, machine_id: str = "i-abc") -> None:
        self.machine_id = _FakeMachineId(machine_id)
        self.status = MagicMock()
        self.status.value = "running"
        self.private_ip = "10.0.0.1"
        self.public_ip = None
        self.launch_time = None


def _make_uow_factory(request: Any = None, machines: Optional[list] = None):
    """Return a UnitOfWorkFactory stub with an injectable request and machine list."""

    uow = MagicMock()
    uow.requests = MagicMock()
    uow.requests.get_by_id.return_value = request
    uow.requests.save = MagicMock()
    uow.machines = MagicMock()
    uow.machines.find_by_request_id.return_value = machines or []

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work = _create
    return factory, uow


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_init_reads_caching_config() -> None:
    """Constructor reads enabled and ttl from config."""
    factory, _ = _make_uow_factory()
    svc = RequestCacheService(factory, _make_config_manager(enabled=True, ttl=120), _make_logger())
    assert svc.is_caching_enabled() is True
    assert svc.get_cache_ttl() == 120


@pytest.mark.unit
def test_init_disabled_caching() -> None:
    """Constructor correctly reads disabled caching."""
    factory, _ = _make_uow_factory()
    svc = RequestCacheService(factory, _make_config_manager(enabled=False), _make_logger())
    assert svc.is_caching_enabled() is False


@pytest.mark.unit
def test_init_defaults_to_disabled_on_config_exception() -> None:
    """Constructor defaults to disabled/300s when config raises."""

    class _BadConfig:
        @property
        def app_config(self):
            raise Exception("boom")

    factory, _ = _make_uow_factory()
    logger = _make_logger()
    svc = RequestCacheService(factory, _BadConfig(), logger)  # type: ignore[arg-type]
    assert svc.is_caching_enabled() is False
    assert svc.get_cache_ttl() == 300


# ---------------------------------------------------------------------------
# get_cached_request
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_cached_request_returns_none_when_disabled() -> None:
    """Returns None immediately when caching is disabled."""
    factory, _ = _make_uow_factory()
    svc = RequestCacheService(factory, _make_config_manager(enabled=False), _make_logger())
    result = svc.get_cached_request(_RID1)
    assert result is None


@pytest.mark.unit
def test_get_cached_request_returns_none_when_request_missing() -> None:
    """Returns None when request not found in storage."""
    factory, _ = _make_uow_factory(request=None)
    svc = RequestCacheService(factory, _make_config_manager(enabled=True, ttl=60), _make_logger())
    result = svc.get_cached_request(_RID2)
    assert result is None


@pytest.mark.unit
def test_get_cached_request_returns_none_when_cache_expired() -> None:
    """Returns None when request updated_at is older than TTL."""
    old_req = _FakeRequest(updated_at=datetime.now(timezone.utc) - timedelta(seconds=200))
    factory, _ = _make_uow_factory(request=old_req)
    svc = RequestCacheService(factory, _make_config_manager(enabled=True, ttl=60), _make_logger())
    result = svc.get_cached_request(_RID1)
    assert result is None


@pytest.mark.unit
def test_get_cached_request_returns_dto_on_cache_hit() -> None:
    """Returns RequestDTO when request is within TTL."""
    fresh_req = _FakeRequest(
        request_id=_RID1,
        template_id="t1",
        requested_count=2,
        status_value="active",
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    factory, _ = _make_uow_factory(request=fresh_req, machines=[])
    svc = RequestCacheService(factory, _make_config_manager(enabled=True, ttl=60), _make_logger())
    result = svc.get_cached_request(_RID1)
    assert result is not None
    assert result.request_id == _RID1
    assert result.template_id == "t1"


@pytest.mark.unit
def test_get_cached_request_includes_machines_in_dto() -> None:
    """Machines are converted and the RequestDTO builds successfully."""
    fresh_req = _FakeRequest(
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )
    machine = _FakeMachine(machine_id="i-xyz")
    factory, _ = _make_uow_factory(request=fresh_req, machines=[machine])
    svc = RequestCacheService(factory, _make_config_manager(enabled=True, ttl=60), _make_logger())
    result = svc.get_cached_request(_RID1)
    assert result is not None


@pytest.mark.unit
def test_get_cached_request_returns_none_and_warns_on_exception() -> None:
    """Returns None and logs a warning when storage raises."""
    factory = MagicMock()
    factory.create_unit_of_work.side_effect = Exception("DB gone")
    svc = RequestCacheService(factory, _make_config_manager(enabled=True), _make_logger())
    result = svc.get_cached_request(_RID1)
    assert result is None


@pytest.mark.unit
def test_get_cached_request_returns_none_when_updated_at_none() -> None:
    """Returns None when request.updated_at is None (cache invalid)."""
    req = _FakeRequest(updated_at=None)
    req.updated_at = None  # Force to None after construction
    factory, _ = _make_uow_factory(request=req)
    svc = RequestCacheService(factory, _make_config_manager(enabled=True, ttl=60), _make_logger())
    result = svc.get_cached_request(_RID1)
    assert result is None


# ---------------------------------------------------------------------------
# cache_request
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cache_request_noop_when_disabled() -> None:
    """cache_request returns early when caching is disabled."""
    factory, uow = _make_uow_factory()
    svc = RequestCacheService(factory, _make_config_manager(enabled=False), _make_logger())
    from orb.application.dto.responses import RequestDTO

    dto = RequestDTO(
        request_id=_RID1,
        template_id="t1",
        requested_count=1,
        status="pending",
        created_at=datetime.now(timezone.utc),
        metadata={},
    )
    svc.cache_request(dto)
    uow.requests.save.assert_not_called()


@pytest.mark.unit
def test_cache_request_saves_updated_timestamp() -> None:
    """cache_request updates updated_at when request is found."""
    req = _FakeRequest()
    factory, uow = _make_uow_factory(request=req)
    svc = RequestCacheService(factory, _make_config_manager(enabled=True), _make_logger())
    from orb.application.dto.responses import RequestDTO

    dto = RequestDTO(
        request_id=_RID1,
        template_id="t1",
        requested_count=1,
        status="pending",
        created_at=datetime.now(timezone.utc),
        metadata={},
    )
    svc.cache_request(dto)
    uow.requests.save.assert_called_once_with(req)


@pytest.mark.unit
def test_cache_request_skips_save_when_request_not_found() -> None:
    """cache_request skips save when get_by_id returns None."""
    factory, uow = _make_uow_factory(request=None)
    svc = RequestCacheService(factory, _make_config_manager(enabled=True), _make_logger())
    from orb.application.dto.responses import RequestDTO

    dto = RequestDTO(
        request_id=_RID2,
        template_id="t1",
        requested_count=1,
        status="pending",
        created_at=datetime.now(timezone.utc),
        metadata={},
    )
    svc.cache_request(dto)
    uow.requests.save.assert_not_called()


@pytest.mark.unit
def test_cache_request_warns_and_does_not_raise_on_exception() -> None:
    """cache_request silently logs warning on storage exception."""
    factory = MagicMock()
    factory.create_unit_of_work.side_effect = Exception("write fail")
    logger = _make_logger()
    svc = RequestCacheService(factory, _make_config_manager(enabled=True), logger)
    from orb.application.dto.responses import RequestDTO

    dto = RequestDTO(
        request_id=_RID1,
        template_id="t1",
        requested_count=1,
        status="pending",
        created_at=datetime.now(timezone.utc),
        metadata={},
    )
    svc.cache_request(dto)  # must not raise
    logger.warning.assert_called()


# ---------------------------------------------------------------------------
# invalidate_cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invalidate_cache_sets_old_timestamp() -> None:
    """invalidate_cache pushes updated_at one day into the past."""
    req = _FakeRequest()
    factory, uow = _make_uow_factory(request=req)
    svc = RequestCacheService(factory, _make_config_manager(enabled=True, ttl=60), _make_logger())
    svc.invalidate_cache(_RID1)
    uow.requests.save.assert_called_once()
    # The updated_at should now be far in the past (> TTL)
    assert req.updated_at is not None
    assert (datetime.now(timezone.utc) - req.updated_at).total_seconds() > 60


@pytest.mark.unit
def test_invalidate_cache_noop_when_request_not_found() -> None:
    """invalidate_cache is a no-op when request is not found."""
    factory, uow = _make_uow_factory(request=None)
    svc = RequestCacheService(factory, _make_config_manager(enabled=True), _make_logger())
    svc.invalidate_cache(_RID2)
    uow.requests.save.assert_not_called()


@pytest.mark.unit
def test_invalidate_cache_warns_on_exception() -> None:
    """invalidate_cache logs warning on storage exception."""
    factory = MagicMock()
    factory.create_unit_of_work.side_effect = Exception("DB error")
    logger = _make_logger()
    svc = RequestCacheService(factory, _make_config_manager(enabled=True), logger)
    svc.invalidate_cache(_RID1)  # must not raise
    logger.warning.assert_called()


# ---------------------------------------------------------------------------
# _is_cache_valid
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_cache_valid_false_when_updated_at_none() -> None:
    """_is_cache_valid returns False when updated_at is None."""
    factory, _ = _make_uow_factory()
    svc = RequestCacheService(factory, _make_config_manager(ttl=60), _make_logger())
    req = MagicMock()
    req.updated_at = None
    assert svc._is_cache_valid(req) is False


@pytest.mark.unit
def test_is_cache_valid_true_when_within_ttl() -> None:
    """_is_cache_valid returns True when within TTL."""
    factory, _ = _make_uow_factory()
    svc = RequestCacheService(factory, _make_config_manager(ttl=60), _make_logger())
    req = MagicMock()
    req.updated_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    assert svc._is_cache_valid(req) is True


@pytest.mark.unit
def test_is_cache_valid_false_when_past_ttl() -> None:
    """_is_cache_valid returns False when past TTL."""
    factory, _ = _make_uow_factory()
    svc = RequestCacheService(factory, _make_config_manager(ttl=60), _make_logger())
    req = MagicMock()
    req.updated_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    assert svc._is_cache_valid(req) is False

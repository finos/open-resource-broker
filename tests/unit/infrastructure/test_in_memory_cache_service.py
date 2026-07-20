"""Unit tests for caching/in_memory_cache_service.py."""

import asyncio
import time

import pytest

from orb.infrastructure.caching.in_memory_cache_service import InMemoryCacheService


def _run(coro):
    """Execute a coroutine synchronously on a fresh event loop.

    Using a dedicated loop per call avoids interference from other tests in the
    suite that close or replace the shared default event loop.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.mark.unit
class TestInMemoryCacheServiceGet:
    """Tests for async get."""

    def test_get_missing_key_returns_none(self) -> None:
        svc = InMemoryCacheService()
        assert _run(svc.get("no-such-key")) is None

    def test_get_returns_stored_value(self) -> None:
        svc = InMemoryCacheService(default_ttl=None)
        _run(svc.set("k", "v"))
        assert _run(svc.get("k")) == "v"

    def test_get_returns_none_for_expired_entry(self) -> None:
        svc = InMemoryCacheService()
        _run(svc.set("k", "val", ttl=0))
        # TTL of 0 means expiry == monotonic time at set — should already be expired
        time.sleep(0.001)
        assert _run(svc.get("k")) is None

    def test_get_cleans_up_expired_entry(self) -> None:
        svc = InMemoryCacheService()
        _run(svc.set("k", "val", ttl=0))
        time.sleep(0.001)
        _run(svc.get("k"))  # triggers cleanup
        assert "k" not in svc._store

    def test_get_non_expired_value_returned(self) -> None:
        svc = InMemoryCacheService(default_ttl=60)
        _run(svc.set("x", 123))
        assert _run(svc.get("x")) == 123


@pytest.mark.unit
class TestInMemoryCacheServiceSet:
    """Tests for async set."""

    def test_set_uses_provided_ttl(self) -> None:
        svc = InMemoryCacheService(default_ttl=300)
        _run(svc.set("k", "v", ttl=10))
        _, expiry = svc._store["k"]
        assert expiry is not None
        # Expiry should be roughly now + 10 seconds
        assert abs(expiry - (time.monotonic() + 10)) < 1

    def test_set_uses_default_ttl_when_ttl_not_specified(self) -> None:
        svc = InMemoryCacheService(default_ttl=42)
        _run(svc.set("k", "v"))
        _, expiry = svc._store["k"]
        assert expiry is not None
        assert abs(expiry - (time.monotonic() + 42)) < 1

    def test_set_with_no_ttl_stores_without_expiry(self) -> None:
        svc = InMemoryCacheService(default_ttl=None)
        _run(svc.set("k", "val", ttl=None))
        _, expiry = svc._store["k"]
        assert expiry is None

    def test_set_overwrites_existing_value(self) -> None:
        svc = InMemoryCacheService(default_ttl=None)
        _run(svc.set("k", "first"))
        _run(svc.set("k", "second"))
        assert _run(svc.get("k")) == "second"


@pytest.mark.unit
class TestInMemoryCacheServiceDelete:
    """Tests for async delete."""

    def test_delete_removes_existing_key(self) -> None:
        svc = InMemoryCacheService(default_ttl=None)
        _run(svc.set("k", "v"))
        _run(svc.delete("k"))
        assert _run(svc.get("k")) is None

    def test_delete_missing_key_does_not_raise(self) -> None:
        svc = InMemoryCacheService()
        _run(svc.delete("nonexistent"))  # should not raise


@pytest.mark.unit
class TestInMemoryCacheServiceClear:
    """Tests for async clear."""

    def test_clear_empties_all_entries(self) -> None:
        svc = InMemoryCacheService(default_ttl=None)
        _run(svc.set("a", 1))
        _run(svc.set("b", 2))
        _run(svc.clear())
        assert svc._store == {}


@pytest.mark.unit
class TestInMemoryCacheServiceExists:
    """Tests for async exists."""

    def test_exists_returns_true_for_present_key(self) -> None:
        svc = InMemoryCacheService(default_ttl=None)
        _run(svc.set("k", "v"))
        assert _run(svc.exists("k")) is True

    def test_exists_returns_false_for_missing_key(self) -> None:
        svc = InMemoryCacheService()
        assert _run(svc.exists("ghost")) is False

    def test_exists_returns_false_for_expired_key(self) -> None:
        svc = InMemoryCacheService()
        _run(svc.set("k", "v", ttl=0))
        time.sleep(0.001)
        assert _run(svc.exists("k")) is False


@pytest.mark.unit
class TestInMemoryCacheServiceSyncMethods:
    """Tests for synchronous cache_request / get_cached_request methods."""

    def test_cache_request_and_get_cached_request(self) -> None:
        svc = InMemoryCacheService(default_ttl=300)
        dto = {"id": "r-1", "status": "pending"}
        svc.cache_request("r-1", dto)
        result = svc.get_cached_request("r-1")
        assert result == dto

    def test_get_cached_request_missing_returns_none(self) -> None:
        svc = InMemoryCacheService()
        assert svc.get_cached_request("unknown") is None

    def test_get_cached_request_expired_returns_none(self) -> None:
        svc = InMemoryCacheService(default_ttl=0)
        svc.cache_request("r-exp", {"data": "x"})
        time.sleep(0.001)
        assert svc.get_cached_request("r-exp") is None

    def test_get_cached_request_expired_cleans_up_entry(self) -> None:
        svc = InMemoryCacheService(default_ttl=0)
        svc.cache_request("r-exp2", {"data": "y"})
        time.sleep(0.001)
        svc.get_cached_request("r-exp2")
        assert "request:r-exp2" not in svc._store

    def test_is_caching_enabled_returns_true(self) -> None:
        svc = InMemoryCacheService()
        assert svc.is_caching_enabled() is True

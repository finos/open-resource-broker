"""Unit tests for CacheServiceAdapter."""

from unittest.mock import MagicMock

import pytest

from orb.infrastructure.adapters.cache_adapter import CacheServiceAdapter

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class TestGet:
    async def test_returns_value_when_cache_supports_get(self):
        cache = MagicMock()
        cache.get.return_value = "cached-value"
        adapter = CacheServiceAdapter(cache)

        result = await adapter.get("key")

        cache.get.assert_called_once_with("key")
        assert result == "cached-value"

    async def test_returns_none_when_cache_has_no_get(self):
        cache = object()
        adapter = CacheServiceAdapter(cache)

        result = await adapter.get("key")

        assert result is None


class TestSet:
    async def test_sets_value_with_ttl(self):
        cache = MagicMock()
        adapter = CacheServiceAdapter(cache)

        await adapter.set("key", "value", ttl=60)

        cache.set.assert_called_once_with("key", "value", 60)

    async def test_sets_value_without_ttl(self):
        cache = MagicMock()
        adapter = CacheServiceAdapter(cache)

        await adapter.set("key", "value")

        cache.set.assert_called_once_with("key", "value")

    async def test_noop_when_cache_has_no_set(self):
        cache = object()
        adapter = CacheServiceAdapter(cache)

        # Should not raise even though the underlying cache has no `set`.
        await adapter.set("key", "value")


class TestDelete:
    async def test_deletes_value(self):
        cache = MagicMock()
        adapter = CacheServiceAdapter(cache)

        await adapter.delete("key")

        cache.delete.assert_called_once_with("key")

    async def test_noop_when_cache_has_no_delete(self):
        cache = object()
        adapter = CacheServiceAdapter(cache)

        await adapter.delete("key")


class TestClear:
    async def test_clears_cache(self):
        cache = MagicMock()
        adapter = CacheServiceAdapter(cache)

        await adapter.clear()

        cache.clear.assert_called_once_with()

    async def test_noop_when_cache_has_no_clear(self):
        cache = object()
        adapter = CacheServiceAdapter(cache)

        await adapter.clear()


class TestExists:
    async def test_delegates_to_cache_exists(self):
        cache = MagicMock()
        cache.exists.return_value = True
        adapter = CacheServiceAdapter(cache)

        result = await adapter.exists("key")

        cache.exists.assert_called_once_with("key")
        assert result is True

    async def test_falls_back_to_get_when_no_exists_method(self):
        cache = MagicMock(spec=["get"])
        cache.get.return_value = "value"
        adapter = CacheServiceAdapter(cache)

        result = await adapter.exists("key")

        cache.get.assert_called_once_with("key")
        assert result is True

    async def test_falls_back_to_get_returns_false_when_missing(self):
        cache = MagicMock(spec=["get"])
        cache.get.return_value = None
        adapter = CacheServiceAdapter(cache)

        result = await adapter.exists("key")

        assert result is False

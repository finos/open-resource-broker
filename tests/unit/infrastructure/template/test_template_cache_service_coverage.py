"""Coverage-gap tests for TemplateCacheService implementations.

Targets the branches missed in existing tests:
- NoOpTemplateCacheService.get_or_load (sync + async loader)
- NoOpTemplateCacheService.get_all / put / is_cached
- TTLTemplateCacheService.get_or_load (cache hit, cache miss, async loader)
- TTLTemplateCacheService.is_cached / invalidate / get_cache_age / get_cache_size
- TTLTemplateCacheService._is_cache_valid (expired TTL)
- AutoRefreshTemplateCacheService.invalidate (timer cancellation)
- create_template_cache_service factory
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.template.dtos import TemplateDTO
from orb.infrastructure.template.template_cache_service import (
    AutoRefreshTemplateCacheService,
    NoOpTemplateCacheService,
    TTLTemplateCacheService,
    create_template_cache_service,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dto(tid: str = "t1") -> TemplateDTO:
    return TemplateDTO(template_id=tid)


def _sync_loader(templates: list[TemplateDTO]):
    def loader():
        return templates

    return loader


async def _async_loader_coro(templates: list[TemplateDTO]) -> list[TemplateDTO]:
    return templates


def _async_loader(templates: list[TemplateDTO]):
    async def loader():
        return templates

    return loader


def _make_logger() -> MagicMock:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.error = MagicMock()
    return logger


# ---------------------------------------------------------------------------
# NoOpTemplateCacheService
# ---------------------------------------------------------------------------


class TestNoOpTemplateCacheService:
    def test_get_or_load_sync_returns_templates(self):
        templates = [_make_dto("t1"), _make_dto("t2")]
        svc = NoOpTemplateCacheService()
        result = asyncio.run(svc.get_or_load(_sync_loader(templates)))
        assert result == templates

    def test_get_or_load_async_returns_templates(self):
        templates = [_make_dto("a")]
        svc = NoOpTemplateCacheService()
        result = asyncio.run(svc.get_or_load(_async_loader(templates)))
        assert result == templates

    def test_get_or_load_with_logger_calls_debug(self):
        logger = _make_logger()
        svc = NoOpTemplateCacheService(logger=logger)
        asyncio.run(svc.get_or_load(_sync_loader([])))
        logger.debug.assert_called_once()

    def test_get_all_returns_none(self):
        assert NoOpTemplateCacheService().get_all() is None

    def test_put_is_noop(self):
        svc = NoOpTemplateCacheService()
        svc.put("key", _make_dto())  # should not raise

    def test_invalidate_is_noop(self):
        svc = NoOpTemplateCacheService()
        svc.invalidate()  # should not raise

    def test_is_cached_returns_false(self):
        assert NoOpTemplateCacheService().is_cached() is False


# ---------------------------------------------------------------------------
# TTLTemplateCacheService
# ---------------------------------------------------------------------------


class TestTTLTemplateCacheService:
    def test_initial_state_is_not_cached(self):
        svc = TTLTemplateCacheService(ttl_seconds=60)
        assert svc.is_cached() is False

    def test_get_or_load_populates_cache(self):
        templates = [_make_dto("t1")]
        svc = TTLTemplateCacheService(ttl_seconds=60)
        result = asyncio.run(svc.get_or_load(_sync_loader(templates)))
        assert result == templates
        assert svc.is_cached() is True

    def test_cache_hit_returns_same_templates(self):
        templates = [_make_dto("t1")]
        load_count = [0]

        def counting_loader():
            load_count[0] += 1
            return templates

        svc = TTLTemplateCacheService(ttl_seconds=60)
        asyncio.run(svc.get_or_load(counting_loader))
        result2 = asyncio.run(svc.get_or_load(counting_loader))
        assert result2 == templates
        assert load_count[0] == 1  # only loaded once

    def test_cache_miss_after_invalidate(self):
        templates = [_make_dto("t1")]
        svc = TTLTemplateCacheService(ttl_seconds=60)
        asyncio.run(svc.get_or_load(_sync_loader(templates)))
        svc.invalidate()
        assert svc.is_cached() is False

    def test_invalidate_clears_templates(self):
        svc = TTLTemplateCacheService(ttl_seconds=60)
        asyncio.run(svc.get_or_load(_sync_loader([_make_dto()])))
        svc.invalidate()
        assert svc.get_cache_size() == 0

    def test_invalidate_with_logger_calls_debug(self):
        logger = _make_logger()
        svc = TTLTemplateCacheService(ttl_seconds=60, logger=logger)
        asyncio.run(svc.get_or_load(_sync_loader([_make_dto()])))
        svc.invalidate()
        # logger.debug should have been called during invalidate
        assert logger.debug.call_count >= 1

    def test_get_cache_age_none_when_not_cached(self):
        svc = TTLTemplateCacheService(ttl_seconds=60)
        assert svc.get_cache_age() is None

    def test_get_cache_age_returns_timedelta_when_cached(self):
        svc = TTLTemplateCacheService(ttl_seconds=60)
        asyncio.run(svc.get_or_load(_sync_loader([_make_dto()])))
        age = svc.get_cache_age()
        assert isinstance(age, timedelta)
        assert age.total_seconds() >= 0

    def test_get_cache_size_zero_when_empty(self):
        svc = TTLTemplateCacheService(ttl_seconds=60)
        assert svc.get_cache_size() == 0

    def test_get_cache_size_returns_count(self):
        svc = TTLTemplateCacheService(ttl_seconds=60)
        asyncio.run(svc.get_or_load(_sync_loader([_make_dto("a"), _make_dto("b")])))
        assert svc.get_cache_size() == 2

    def test_cache_expired_reloads(self):
        """Expired cache (TTL=0) always reloads."""
        call_count = [0]

        def loader():
            call_count[0] += 1
            return [_make_dto()]

        svc = TTLTemplateCacheService(ttl_seconds=0)
        asyncio.run(svc.get_or_load(loader))
        # Manually expire cache by setting _cache_time to past
        svc._cache_time = datetime.now() - timedelta(seconds=10)
        asyncio.run(svc.get_or_load(loader))
        assert call_count[0] == 2

    def test_async_loader_supported(self):
        templates = [_make_dto("async_t")]
        svc = TTLTemplateCacheService(ttl_seconds=60)
        result = asyncio.run(svc.get_or_load(_async_loader(templates)))
        assert result == templates

    def test_cache_hit_logs_debug(self):
        logger = _make_logger()
        svc = TTLTemplateCacheService(ttl_seconds=60, logger=logger)
        asyncio.run(svc.get_or_load(_sync_loader([_make_dto()])))
        # Second call hits cache
        asyncio.run(svc.get_or_load(_sync_loader([_make_dto()])))
        # At least one debug call was for cache hit
        debug_msgs = [str(c) for c in logger.debug.call_args_list]
        assert any("hit" in m.lower() for m in debug_msgs)


# ---------------------------------------------------------------------------
# AutoRefreshTemplateCacheService
# ---------------------------------------------------------------------------


class TestAutoRefreshTemplateCacheService:
    def test_get_or_load_works_without_auto_refresh(self):
        templates = [_make_dto("ar")]
        svc = AutoRefreshTemplateCacheService(ttl_seconds=60, auto_refresh=False)
        result = asyncio.run(svc.get_or_load(_sync_loader(templates)))
        assert result == templates

    def test_invalidate_cancels_timer(self):
        svc = AutoRefreshTemplateCacheService(ttl_seconds=60, auto_refresh=True)
        asyncio.run(svc.get_or_load(_sync_loader([_make_dto()])))
        # Timer may or may not be scheduled; invalidate should not raise
        svc.invalidate()
        assert svc._refresh_timer is None

    def test_invalidate_clears_cache(self):
        svc = AutoRefreshTemplateCacheService(ttl_seconds=60, auto_refresh=False)
        asyncio.run(svc.get_or_load(_sync_loader([_make_dto()])))
        assert svc.is_cached()
        svc.invalidate()
        assert not svc.is_cached()


# ---------------------------------------------------------------------------
# create_template_cache_service factory
# ---------------------------------------------------------------------------


class TestCreateTemplateCacheServiceFactory:
    def test_noop_type(self):
        svc = create_template_cache_service("noop")
        assert isinstance(svc, NoOpTemplateCacheService)

    def test_ttl_type(self):
        svc = create_template_cache_service("ttl", ttl_seconds=120)
        assert isinstance(svc, TTLTemplateCacheService)

    def test_auto_refresh_type(self):
        svc = create_template_cache_service("auto_refresh", ttl_seconds=60)
        assert isinstance(svc, AutoRefreshTemplateCacheService)
        svc.invalidate()  # cleanup any timer

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported cache type"):
            create_template_cache_service("nonexistent")

    def test_logger_passed_through(self):
        logger = _make_logger()
        svc = create_template_cache_service("ttl", logger=logger, ttl_seconds=30)
        assert svc._logger is logger

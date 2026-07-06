"""Unit tests for the rate-limit middleware's LRU eviction policy."""

from __future__ import annotations

import asyncio

import pytest

from orb.api.middleware.rate_limit_middleware import RateLimitMiddleware


@pytest.mark.unit
class TestRateLimitLRUEviction:
    """The bucket dict must stay bounded under a churn of identities."""

    def test_lru_eviction_caps_dict_size(self) -> None:
        async def _run() -> None:
            middleware = RateLimitMiddleware(
                app=lambda *_: None,
                rate_limiting_config={
                    "enabled": True,
                    "requests_per_minute": 100,
                    "max_buckets": 3,
                },
            )

            # Insert 5 distinct identities; cap is 3 → oldest 2 should be
            # evicted, leaving only the most recent 3.
            for i in range(5):
                allowed, _ = await middleware._check_and_consume(f"ip:{i}")
                assert allowed is True

            assert len(middleware._buckets) == 3
            assert list(middleware._buckets.keys()) == ["ip:2", "ip:3", "ip:4"]

        asyncio.run(_run())

    def test_touch_promotes_to_most_recent(self) -> None:
        async def _run() -> None:
            middleware = RateLimitMiddleware(
                app=lambda *_: None,
                rate_limiting_config={
                    "enabled": True,
                    "requests_per_minute": 100,
                    "max_buckets": 3,
                },
            )

            for i in range(3):
                await middleware._check_and_consume(f"ip:{i}")

            # Touch ip:0 → it moves to the end. Adding ip:3 should now
            # evict ip:1 (the new LRU), not ip:0.
            await middleware._check_and_consume("ip:0")
            await middleware._check_and_consume("ip:3")

            assert "ip:1" not in middleware._buckets
            assert "ip:0" in middleware._buckets
            assert "ip:3" in middleware._buckets

        asyncio.run(_run())

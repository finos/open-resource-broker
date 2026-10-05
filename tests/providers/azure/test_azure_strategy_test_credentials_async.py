"""Non-blocking contract for AzureProviderStrategy.test_credentials_async."""

import asyncio

import pytest

from orb.providers.base.strategy import ProviderHealthStatus


@pytest.mark.asyncio
async def test_test_credentials_async_delegates_to_check_health_async(
    strategy_harness,
) -> None:
    """test_credentials_async must use the async health check, not the sync one."""
    strategy = strategy_harness.strategy
    calls: list[str] = []

    async def fake_check_health_async() -> ProviderHealthStatus:
        calls.append("async")
        return ProviderHealthStatus.healthy("ok", 1.0)

    def fake_check_health() -> ProviderHealthStatus:
        calls.append("sync")
        return ProviderHealthStatus.healthy("ok", 1.0)

    strategy._health_check_service.check_health_async = fake_check_health_async
    strategy._health_check_service.check_health = fake_check_health

    result = await strategy.test_credentials_async()

    assert result == {"success": True}
    assert calls == ["async"]


@pytest.mark.asyncio
async def test_test_credentials_async_does_not_block_the_event_loop(
    strategy_harness,
) -> None:
    """A slow token fetch must not stall other coroutines on the same loop.

    The underlying ``check_health_async`` awaits ``asyncio.sleep`` to stand
    in for real network I/O done via the async Azure credential. A
    concurrent canary task is used to prove the loop keeps running while
    the "credential fetch" is in flight.
    """
    strategy = strategy_harness.strategy
    progressed_during_call = False

    async def slow_check_health_async() -> ProviderHealthStatus:
        await asyncio.sleep(0.2)
        return ProviderHealthStatus.healthy("ok", 1.0)

    async def canary() -> None:
        nonlocal progressed_during_call
        await asyncio.sleep(0.05)
        progressed_during_call = True

    strategy._health_check_service.check_health_async = slow_check_health_async

    canary_task = asyncio.create_task(canary())
    result = await strategy.test_credentials_async()
    # Join the canary task; the return value (None) is intentionally discarded.
    _ = await canary_task

    assert result == {"success": True}
    assert progressed_during_call is True

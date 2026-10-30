"""The fallback retry wait must yield to the event loop instead of blocking it."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.base.strategy.fallback_strategy import (
    FallbackConfig,
    FallbackMode,
    FallbackProviderStrategy,
)
from orb.providers.base.strategy.provider_strategy import (
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
)


def _strategy(name: str, results: list[ProviderResult]) -> MagicMock:
    s = MagicMock()
    s.provider_type = name
    s.is_initialized = True
    s.execute_operation = AsyncMock(side_effect=results)
    return s


@pytest.mark.asyncio
async def test_retry_wait_yields_to_other_tasks():
    primary = _strategy(
        "primary",
        [ProviderResult.error_result("fail", "ERR"), ProviderResult.success_result({})],
    )
    config = FallbackConfig(
        mode=FallbackMode.RETRY_THEN_FALLBACK, max_retries=1, retry_delay_seconds=0.2
    )
    strategy = FallbackProviderStrategy(
        logger=MagicMock(),
        primary_strategy=primary,
        fallback_strategies=[_strategy("fb", [])],
        config=config,
    )
    strategy._initialized = True

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    operation = ProviderOperation(operation_type=ProviderOperationType.HEALTH_CHECK, parameters={})
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await strategy._execute_with_retry_fallback(operation)
    elapsed = loop.time() - start
    task.cancel()

    assert result.success
    assert elapsed >= 0.2  # the configured delay is still honoured
    assert ticks >= 5  # other tasks ran during the wait

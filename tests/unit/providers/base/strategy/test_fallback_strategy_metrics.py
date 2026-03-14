"""Unit tests for FallbackProviderStrategy metrics emission."""

import pytest
from unittest.mock import AsyncMock, MagicMock, call

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


def _make_strategy(provider_type: str, success: bool) -> MagicMock:
    """Build a mock ProviderStrategy."""
    strategy = MagicMock()
    strategy.provider_type = provider_type
    strategy.is_initialized = True
    result = ProviderResult.success_result({}) if success else ProviderResult.error_result("fail", "ERR")
    strategy.execute_operation = AsyncMock(return_value=result)
    return strategy


def _make_operation() -> ProviderOperation:
    return ProviderOperation(operation_type=ProviderOperationType.HEALTH_CHECK, parameters={})


def _make_fallback_strategy(primary, fallbacks, metrics=None, mode=FallbackMode.IMMEDIATE):
    logger = MagicMock()
    config = FallbackConfig(mode=mode)
    strategy = FallbackProviderStrategy(
        logger=logger,
        primary_strategy=primary,
        fallback_strategies=fallbacks,
        config=config,
        metrics=metrics,
    )
    strategy._initialized = True
    return strategy


# ---------------------------------------------------------------------------
# provider_fallback_total
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fallback_increments_provider_fallback_total():
    """When primary fails and fallback succeeds, provider_fallback_total is incremented."""
    primary = _make_strategy("primary-provider", success=False)
    fallback = _make_strategy("fallback-provider", success=True)
    metrics = MagicMock()

    strategy = _make_fallback_strategy(primary, [fallback], metrics=metrics)
    await strategy.execute_operation(_make_operation())

    metrics.increment.assert_called_once_with(
        "provider_fallback_total",
        labels={"primary": "primary-provider", "fallback": "fallback-provider"},
    )


@pytest.mark.asyncio
async def test_no_fallback_metric_when_primary_succeeds():
    """When primary succeeds, provider_fallback_total is NOT incremented."""
    primary = _make_strategy("primary-provider", success=True)
    fallback = _make_strategy("fallback-provider", success=True)
    metrics = MagicMock()

    strategy = _make_fallback_strategy(primary, [fallback], metrics=metrics)
    await strategy.execute_operation(_make_operation())

    metrics.increment.assert_not_called()


@pytest.mark.asyncio
async def test_no_metrics_injected_does_not_raise():
    """FallbackProviderStrategy works correctly when no MetricsCollector is injected."""
    primary = _make_strategy("primary-provider", success=False)
    fallback = _make_strategy("fallback-provider", success=True)

    strategy = _make_fallback_strategy(primary, [fallback], metrics=None)
    result = await strategy.execute_operation(_make_operation())

    assert result.success


# ---------------------------------------------------------------------------
# circuit_breaker_opened_total / circuit_breaker_closed_total
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_breaker_opened_metric_emitted():
    """When failures reach threshold, circuit_breaker_opened_total is incremented."""
    primary = _make_strategy("primary-provider", success=False)
    fallback = _make_strategy("fallback-provider", success=True)
    metrics = MagicMock()

    config = FallbackConfig(mode=FallbackMode.CIRCUIT_BREAKER, circuit_breaker_threshold=1)
    logger = MagicMock()
    strategy = FallbackProviderStrategy(
        logger=logger,
        primary_strategy=primary,
        fallback_strategies=[fallback],
        config=config,
        metrics=metrics,
    )
    strategy._initialized = True

    await strategy.execute_operation(_make_operation())

    metrics.increment.assert_any_call(
        "circuit_breaker_opened_total",
        labels={"provider": "primary-provider"},
    )


@pytest.mark.asyncio
async def test_circuit_breaker_closed_metric_emitted():
    """When circuit recovers via HALF_OPEN, circuit_breaker_closed_total is incremented."""
    from orb.providers.base.strategy.fallback_strategy import CircuitState

    primary = _make_strategy("primary-provider", success=True)
    fallback = _make_strategy("fallback-provider", success=True)
    metrics = MagicMock()

    config = FallbackConfig(
        mode=FallbackMode.CIRCUIT_BREAKER,
        circuit_breaker_threshold=1,
        circuit_breaker_timeout_seconds=60.0,
    )
    logger = MagicMock()
    strategy = FallbackProviderStrategy(
        logger=logger,
        primary_strategy=primary,
        fallback_strategies=[fallback],
        config=config,
        metrics=metrics,
    )
    strategy._initialized = True

    # Force circuit into HALF_OPEN state (simulates timeout elapsed after OPEN)
    strategy._circuit_state.state = CircuitState.HALF_OPEN

    await strategy.execute_operation(_make_operation())

    metrics.increment.assert_any_call(
        "circuit_breaker_closed_total",
        labels={"provider": "primary-provider"},
    )

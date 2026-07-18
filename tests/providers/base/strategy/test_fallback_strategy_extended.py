"""Extended unit tests for FallbackProviderStrategy — targeting uncovered branches.

Covers:
- _update_health_status when interval not yet elapsed (line 281-282)
- _execute_with_retry_fallback retry loop with sleep (lines 435-450)
- _execute_health_based with exception from primary (lines 473-479)
- _execute_fallback_chain with exception from fallback (lines 526-528)
- _update_health_status exception path (lines 579-584)
- CircuitBreakerState fallback metrics path (lines 618-619)
- cleanup primary-raises exception swallowed (lines 684-685)
- FallbackProviderStrategy __str__ / __repr__ (lines 728-729 / 744-757)
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from orb.providers.base.strategy.fallback_strategy import (
    FallbackConfig,
    FallbackMode,
    FallbackProviderStrategy,
)
from orb.providers.base.strategy.provider_strategy import (
    ProviderOperationType,
    ProviderResult,
)
from tests.providers.base.strategy.conftest import ConcreteProviderStrategy, make_op


def _run(coro):
    """Run coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# _update_health_status: interval not elapsed (lines 281-282)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackUpdateHealthStatusNotElapsed:
    def test_skips_health_check_when_interval_not_elapsed(self, mock_logger):
        primary = ConcreteProviderStrategy("hc_primary")
        fallback = ConcreteProviderStrategy("hc_fallback")
        cfg = FallbackConfig(
            mode=FallbackMode.HEALTH_BASED,
            health_check_interval_seconds=9999.0,
        )
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        # Set last check to now so interval hasn't elapsed
        s._last_health_check = time.time()
        s._primary_healthy = True

        # _update_health_status should NOT call check_health because interval hasn't passed
        from unittest.mock import patch

        with patch.object(primary, "check_health") as mock_check:
            s._update_health_status()
            mock_check.assert_not_called()


# ---------------------------------------------------------------------------
# _update_health_status: exception path (lines 579-584)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackUpdateHealthStatusException:
    def test_exception_marks_primary_unhealthy(self, mock_logger):
        broken_p = MagicMock()
        broken_p.provider_type = "broken_hc_p"
        broken_p.is_initialized = True
        broken_p.initialize.return_value = True
        broken_p.check_health.side_effect = RuntimeError("health explode")

        fallback = ConcreteProviderStrategy("hc_fb")
        cfg = FallbackConfig(health_check_interval_seconds=0.0)
        s = FallbackProviderStrategy(mock_logger, broken_p, [fallback], config=cfg)
        s._initialized = True
        s._primary_healthy = True
        s._last_health_check = 0.0  # force interval to be elapsed

        s._update_health_status()
        assert s._primary_healthy is False


# ---------------------------------------------------------------------------
# _execute_with_retry_fallback: retry loop with sleeps skipped (lines 435-450)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackRetryLoop:
    def test_retry_attempts_primary_multiple_times(self, mock_logger):
        """Ensure retry loop runs max_retries+1 times before falling back."""
        call_count = {"n": 0}

        class CountingStrategy(ConcreteProviderStrategy):
            async def execute_operation(self, operation):
                call_count["n"] += 1
                return ProviderResult.error_result("always fail", "ERR")

        primary = CountingStrategy("retry_p")
        fallback = ConcreteProviderStrategy(
            "retry_f", operation_result=ProviderResult.success_result({"rescued": True})
        )
        cfg = FallbackConfig(
            mode=FallbackMode.RETRY_THEN_FALLBACK,
            max_retries=2,
            retry_delay_seconds=0,  # no sleep
        )
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        result = _run(s.execute_operation(make_op()))
        assert result.success
        # primary called max_retries + 1 times
        assert call_count["n"] == 3

    def test_retry_exception_from_primary_retries_and_falls_back(self, mock_logger):
        call_count = {"n": 0}

        class ExcStrategy(ConcreteProviderStrategy):
            async def execute_operation(self, operation):
                call_count["n"] += 1
                raise RuntimeError("retry exc")

        primary = ExcStrategy("exc_retry_p")
        fallback = ConcreteProviderStrategy(
            "exc_retry_f",
            operation_result=ProviderResult.success_result({"src": "fallback"}),
        )
        cfg = FallbackConfig(
            mode=FallbackMode.RETRY_THEN_FALLBACK,
            max_retries=1,
            retry_delay_seconds=0,
        )
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        result = _run(s.execute_operation(make_op()))
        assert result.success
        assert call_count["n"] == 2  # max_retries + 1


# ---------------------------------------------------------------------------
# _execute_health_based: exception from primary (lines 473-479)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackHealthBasedException:
    def test_exception_from_primary_marks_unhealthy_and_uses_fallback(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "hb_exc_p", operation_raises=RuntimeError("network error")
        )
        fallback = ConcreteProviderStrategy(
            "hb_exc_f",
            operation_result=ProviderResult.success_result({"recovered": True}),
        )
        cfg = FallbackConfig(mode=FallbackMode.HEALTH_BASED)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        s._primary_healthy = True
        result = _run(s.execute_operation(make_op()))
        assert result.success
        assert s._primary_healthy is False


# ---------------------------------------------------------------------------
# _execute_fallback_chain: exception from fallback (lines 526-528)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackChainException:
    def test_exception_from_fallback_continues_to_next(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "chain_p", operation_result=ProviderResult.error_result("fail", "ERR")
        )
        broken_fallback = ConcreteProviderStrategy(
            "broken_chain_f", operation_raises=RuntimeError("fallback explode")
        )
        good_fallback = ConcreteProviderStrategy(
            "good_chain_f",
            operation_result=ProviderResult.success_result({"ok": True}),
        )
        cfg = FallbackConfig(mode=FallbackMode.IMMEDIATE)
        s = FallbackProviderStrategy(
            mock_logger, primary, [broken_fallback, good_fallback], config=cfg
        )
        s.initialize()
        result = _run(s.execute_operation(make_op()))
        assert result.success

    def test_all_fallbacks_raise_graceful_degradation(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "all_chain_p", operation_result=ProviderResult.error_result("fail", "ERR")
        )
        raising_fb = ConcreteProviderStrategy(
            "raising_fb", operation_raises=RuntimeError("fb boom")
        )
        cfg = FallbackConfig(mode=FallbackMode.IMMEDIATE, enable_graceful_degradation=True)
        s = FallbackProviderStrategy(mock_logger, primary, [raising_fb], config=cfg)
        s.initialize()
        # HEALTH_CHECK operation → graceful degradation returns degraded success
        op = make_op(ProviderOperationType.HEALTH_CHECK)
        result = _run(s.execute_operation(op))
        assert result.success
        assert result.data["status"] == "degraded"


# ---------------------------------------------------------------------------
# Fallback metrics: last_error propagated (lines 618-619)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackChainLastError:
    def test_last_error_from_failed_result_propagated_to_degradation(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "err_prop_p", operation_result=ProviderResult.error_result("primary error msg", "ERR")
        )
        fallback = ConcreteProviderStrategy(
            "err_prop_f",
            operation_result=ProviderResult.error_result("fallback error msg", "ERR"),
        )
        cfg = FallbackConfig(mode=FallbackMode.IMMEDIATE, enable_graceful_degradation=True)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        # For CREATE_INSTANCES the degraded result is an error with DEGRADED_MODE
        op = make_op(ProviderOperationType.CREATE_INSTANCES)
        result = _run(s.execute_operation(op))
        assert not result.success
        assert result.error_code == "DEGRADED_MODE"


# ---------------------------------------------------------------------------
# cleanup: primary raises (lines 684-685)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackCleanupPrimaryRaises:
    def test_cleanup_primary_exception_swallowed(self, mock_logger):
        broken_p = MagicMock()
        broken_p.provider_type = "broken_cleanup_p"
        broken_p.is_initialized = True
        broken_p.initialize.return_value = True
        broken_p.cleanup.side_effect = RuntimeError("primary cleanup fail")
        fallback = ConcreteProviderStrategy("ok_cleanup_f")
        s = FallbackProviderStrategy(mock_logger, broken_p, [fallback])
        s.initialize()
        s.cleanup()  # Must not raise
        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# __str__ / __repr__ coverage (lines 728-729 / 744-757)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackStrategyStrRepr:
    def test_str_contains_mode(self, mock_logger):
        primary = ConcreteProviderStrategy("str_mode_p")
        fallback = ConcreteProviderStrategy("str_mode_f")
        cfg = FallbackConfig(mode=FallbackMode.CIRCUIT_BREAKER)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        assert "circuit_breaker" in str(s)

    def test_repr_contains_fallbacks_list(self, mock_logger):
        primary = ConcreteProviderStrategy("repr_p2")
        fallback = ConcreteProviderStrategy("repr_f2")
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        r = repr(s)
        assert "repr_f2" in r
        assert "fallbacks" in r

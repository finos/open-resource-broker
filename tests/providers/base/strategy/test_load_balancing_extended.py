"""Extended unit tests for LoadBalancingProviderStrategy — targeting uncovered branches.

Covers:
- execute_operation when _select_strategy returns None (no strategies)
- All algorithm branches: WEIGHTED_ROUND_ROBIN, WEIGHTED_RANDOM, ADAPTIVE, and else/default
- check_health delegates to get_health_status
- shutdown when health-check thread is alive
- Outer except branch in execute_operation
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.base.strategy.load_balancing.algorithms import LoadBalancingAlgorithm
from orb.providers.base.strategy.load_balancing.config import LoadBalancingConfig
from orb.providers.base.strategy.load_balancing.strategy import (
    LoadBalancingProviderStrategy,
)
from orb.providers.base.strategy.provider_strategy import ProviderResult
from tests.providers.base.strategy.conftest import ConcreteProviderStrategy, make_op

# ---------------------------------------------------------------------------
# execute_operation — no strategy selected (line 143)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingExecuteNoStrategy:
    def test_returns_error_when_no_strategy_available(self):
        """Force _select_strategy to return None by patching it."""
        s = ConcreteProviderStrategy("no_strat_lb")
        lb = LoadBalancingProviderStrategy(MagicMock(), [s])
        lb.initialize()
        with patch.object(lb, "_select_strategy", return_value=None):
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(lb.execute_operation(make_op()))
            finally:
                loop.close()
        assert not result.success
        assert "No healthy strategies available" in (result.error_message or "")


# ---------------------------------------------------------------------------
# execute_operation — outer except path (lines 178-182)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingOuterExcept:
    def test_outer_exception_returns_error(self):
        """Patch _select_strategy to raise so the outer try/except fires."""
        s = ConcreteProviderStrategy("outer_exc_lb")
        lb = LoadBalancingProviderStrategy(MagicMock(), [s])
        lb.initialize()
        with patch.object(lb, "_select_strategy", side_effect=RuntimeError("outer boom")):
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(lb.execute_operation(make_op()))
            finally:
                loop.close()
        assert not result.success
        assert "Load balancing failed" in (result.error_message or "")


# ---------------------------------------------------------------------------
# Algorithm branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingAlgorithmBranches:
    def _run(self, lb, op=None):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(lb.execute_operation(op or make_op()))
        finally:
            loop.close()

    def test_weighted_round_robin_delegates_to_round_robin(self):
        """WEIGHTED_ROUND_ROBIN falls through to _round_robin_selection (line 244)."""
        s = ConcreteProviderStrategy("wrr_s", operation_result=ProviderResult.success_result({}))
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.WEIGHTED_ROUND_ROBIN)
        lb = LoadBalancingProviderStrategy(MagicMock(), [s], config=cfg)
        lb.initialize()
        result = self._run(lb)
        assert result.success

    def test_weighted_random_delegates_to_random(self):
        """WEIGHTED_RANDOM falls through to _random_selection (line 288)."""
        s = ConcreteProviderStrategy("wrand_s", operation_result=ProviderResult.success_result({}))
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.WEIGHTED_RANDOM)
        lb = LoadBalancingProviderStrategy(MagicMock(), [s], config=cfg)
        lb.initialize()
        result = self._run(lb)
        assert result.success

    def test_adaptive_delegates_to_least_response_time(self):
        """ADAPTIVE calls _adaptive_selection → _least_response_time (line 303)."""
        s = ConcreteProviderStrategy("adapt_s", operation_result=ProviderResult.success_result({}))
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.ADAPTIVE)
        lb = LoadBalancingProviderStrategy(MagicMock(), [s], config=cfg)
        lb.initialize()
        result = self._run(lb)
        assert result.success

    def test_default_else_falls_back_to_round_robin(self):
        """Cover the else branch at end of algorithm selection (line 228-229)."""
        s = ConcreteProviderStrategy(
            "else_rr_s", operation_result=ProviderResult.success_result({})
        )
        cfg = LoadBalancingConfig()
        lb = LoadBalancingProviderStrategy(MagicMock(), [s], config=cfg)
        lb.initialize()
        # Directly call _select_strategy with a patched algorithm value that
        # doesn't match any branch.
        lb._config = MagicMock()
        lb._config.sticky_sessions = False
        lb._config.algorithm = "nonexistent_algo"
        with patch.object(lb, "_round_robin_selection", wraps=lb._round_robin_selection) as spy:
            lb._select_strategy(make_op())
            spy.assert_called_once()


# ---------------------------------------------------------------------------
# check_health delegates to get_health_status (line 374)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingCheckHealthDelegation:
    def test_check_health_returns_same_as_get_health_status(self):
        s = ConcreteProviderStrategy("chk_hlth_lb")
        lb = LoadBalancingProviderStrategy(MagicMock(), [s])
        lb.initialize()
        status_from_check = lb.check_health()
        status_from_get = lb.get_health_status()
        assert status_from_check.is_healthy == status_from_get.is_healthy
        assert status_from_check.status_message == status_from_get.status_message


# ---------------------------------------------------------------------------
# shutdown with live health-check thread (lines 357-358)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingShutdownWithThread:
    def test_shutdown_joins_health_check_thread(self):
        s = ConcreteProviderStrategy("hc_thread_lb")
        lb = LoadBalancingProviderStrategy(MagicMock(), [s])
        lb.initialize()

        # Plant a fake health-check thread that is "alive" and has a join method
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        lb._health_check_thread = mock_thread  # type: ignore[assignment]

        lb.shutdown()
        mock_thread.join.assert_called_once_with(timeout=5.0)
        assert lb._shutdown_event.is_set()

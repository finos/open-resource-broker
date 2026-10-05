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
from orb.providers.base.strategy.provider_strategy import (
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
)
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


# ---------------------------------------------------------------------------
# _select_strategy dispatcher — remaining algorithm branches
# (LEAST_CONNECTIONS, LEAST_RESPONSE_TIME, RANDOM, HASH_BASED)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingSelectStrategyDispatcherBranches:
    def test_least_connections_branch_selected_via_dispatcher(self):
        s = ConcreteProviderStrategy("disp_lc")
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.LEAST_CONNECTIONS)
        lb = LoadBalancingProviderStrategy(MagicMock(), [s], config=cfg)
        lb.initialize()
        selected = lb._select_strategy(make_op())
        assert selected is not None
        assert selected.provider_type == "disp_lc"

    def test_least_response_time_branch_selected_via_dispatcher(self):
        s = ConcreteProviderStrategy("disp_lrt")
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.LEAST_RESPONSE_TIME)
        lb = LoadBalancingProviderStrategy(MagicMock(), [s], config=cfg)
        lb.initialize()
        selected = lb._select_strategy(make_op())
        assert selected is not None
        assert selected.provider_type == "disp_lrt"

    def test_random_branch_selected_via_dispatcher(self):
        s = ConcreteProviderStrategy("disp_rand")
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.RANDOM)
        lb = LoadBalancingProviderStrategy(MagicMock(), [s], config=cfg)
        lb.initialize()
        selected = lb._select_strategy(make_op())
        assert selected is not None
        assert selected.provider_type == "disp_rand"

    def test_hash_based_branch_selected_via_dispatcher(self):
        s = ConcreteProviderStrategy("disp_hash")
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.HASH_BASED)
        lb = LoadBalancingProviderStrategy(MagicMock(), [s], config=cfg)
        lb.initialize()
        selected = lb._select_strategy(make_op())
        assert selected is not None
        assert selected.provider_type == "disp_hash"


# ---------------------------------------------------------------------------
# Sticky session configured but the recorded session strategy is no longer
# among the healthy strategies — falls through to normal algorithm
# selection instead of returning early (branch 205->209).
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingStickySessionFallthrough:
    def test_unknown_session_strategy_falls_through_to_algorithm_selection(self):
        s_a = ConcreteProviderStrategy("sticky_fall_a")
        s_b = ConcreteProviderStrategy("sticky_fall_b")
        cfg = LoadBalancingConfig(
            sticky_sessions=True,
            algorithm=LoadBalancingAlgorithm.ROUND_ROBIN,
        )
        lb = LoadBalancingProviderStrategy(MagicMock(), [s_a, s_b], config=cfg)
        lb.initialize()
        # Session points at a strategy_type that no longer exists among the
        # healthy strategies, so `session_strategy in healthy_strategies` is False.
        lb._sessions["ghost-session"] = "no_such_strategy"
        lb._session_timestamps["ghost-session"] = __import__("time").time()

        op = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK,
            parameters={},
        )
        op.session_id = "ghost-session"  # type: ignore[attr-defined]

        selected = lb._select_strategy(op)
        assert selected is not None
        assert selected.provider_type in ("sticky_fall_a", "sticky_fall_b")


# ---------------------------------------------------------------------------
# _least_connections_selection / _least_response_time_selection — loop
# continuation branch where a later item does NOT beat the current minimum
# (branches 255->253 and 270->268)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingSelectionLoopContinuation:
    def test_least_connections_third_strategy_does_not_win(self):
        s_busy = ConcreteProviderStrategy("lc3_busy")
        s_free = ConcreteProviderStrategy("lc3_free")
        s_mid = ConcreteProviderStrategy("lc3_mid")
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.LEAST_CONNECTIONS)
        lb = LoadBalancingProviderStrategy(MagicMock(), [s_busy, s_free, s_mid], config=cfg)
        lb.initialize()
        lb._stats["lc3_busy"].active_connections = 5
        lb._stats["lc3_free"].active_connections = 0
        lb._stats["lc3_mid"].active_connections = 2
        selected = lb._least_connections_selection(lb._strategies)
        assert selected.provider_type == "lc3_free"

    def test_least_response_time_third_strategy_does_not_win(self):
        s_slow = ConcreteProviderStrategy("lrt3_slow")
        s_fast = ConcreteProviderStrategy("lrt3_fast")
        s_mid = ConcreteProviderStrategy("lrt3_mid")
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.LEAST_RESPONSE_TIME)
        lb = LoadBalancingProviderStrategy(MagicMock(), [s_slow, s_fast, s_mid], config=cfg)
        lb.initialize()
        lb._stats["lrt3_slow"].average_response_time = 200.0
        lb._stats["lrt3_fast"].average_response_time = 10.0
        lb._stats["lrt3_mid"].average_response_time = 50.0
        selected = lb._least_response_time_selection(lb._strategies)
        assert selected.provider_type == "lrt3_fast"


# ---------------------------------------------------------------------------
# Sticky sessions enabled but the operation carries no session_id at all
# (branch 203->209: `if session_id is not None` is False)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingStickySessionsNoSessionId:
    def test_sticky_sessions_enabled_without_session_id_uses_normal_selection(self):
        s = ConcreteProviderStrategy("sticky_no_session_id")
        cfg = LoadBalancingConfig(
            sticky_sessions=True,
            algorithm=LoadBalancingAlgorithm.ROUND_ROBIN,
        )
        lb = LoadBalancingProviderStrategy(MagicMock(), [s], config=cfg)
        lb.initialize()

        # A plain ProviderOperation has no `session_id` attribute at all.
        selected = lb._select_strategy(make_op())
        assert selected is not None
        assert selected.provider_type == "sticky_no_session_id"

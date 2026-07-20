"""Unit tests for LoadBalancingProviderStrategy."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from orb.providers.base.strategy.load_balancing.algorithms import (
    LoadBalancingAlgorithm,
)
from orb.providers.base.strategy.load_balancing.config import LoadBalancingConfig
from orb.providers.base.strategy.load_balancing.stats import StrategyStats
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
# LoadBalancingConfig validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingConfig:
    def test_defaults_are_valid(self):
        cfg = LoadBalancingConfig()
        assert cfg.health_check_interval_seconds > 0
        assert cfg.unhealthy_threshold >= 1
        assert cfg.recovery_threshold >= 1

    def test_rejects_non_positive_health_check_interval(self):
        with pytest.raises(ValueError, match="health_check_interval"):
            LoadBalancingConfig(health_check_interval_seconds=0)

    def test_rejects_zero_unhealthy_threshold(self):
        with pytest.raises(ValueError, match="unhealthy_threshold"):
            LoadBalancingConfig(unhealthy_threshold=0)

    def test_rejects_zero_recovery_threshold(self):
        with pytest.raises(ValueError, match="recovery_threshold"):
            LoadBalancingConfig(recovery_threshold=0)

    def test_rejects_zero_max_connections(self):
        with pytest.raises(ValueError, match="max_connections_per_strategy"):
            LoadBalancingConfig(max_connections_per_strategy=0)

    def test_rejects_zero_weight_adjustment(self):
        with pytest.raises(ValueError, match="weight_adjustment_factor"):
            LoadBalancingConfig(weight_adjustment_factor=0)


# ---------------------------------------------------------------------------
# StrategyStats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStrategyStats:
    def test_initial_success_rate_is_100(self):
        stats = StrategyStats()
        assert stats.success_rate == 100.0

    def test_initial_failure_rate_is_zero(self):
        stats = StrategyStats()
        assert stats.failure_rate == 0.0

    def test_record_request_start_increments_connections(self):
        stats = StrategyStats()
        stats.record_request_start()
        assert stats.active_connections == 1
        assert stats.total_requests == 1

    def test_record_request_end_success_decrements_connections(self):
        stats = StrategyStats()
        stats.record_request_start()
        stats.record_request_end(success=True, response_time_ms=50.0)
        assert stats.active_connections == 0
        assert stats.successful_requests == 1
        assert stats.consecutive_successes == 1
        assert stats.consecutive_failures == 0

    def test_record_request_end_failure(self):
        stats = StrategyStats()
        stats.record_request_start()
        stats.record_request_end(success=False, response_time_ms=100.0)
        assert stats.failed_requests == 1
        assert stats.consecutive_failures == 1
        assert stats.consecutive_successes == 0

    def test_active_connections_floor_at_zero(self):
        stats = StrategyStats()
        stats.record_request_end(success=True, response_time_ms=1.0)  # no start
        assert stats.active_connections == 0

    def test_average_response_time_computed_correctly(self):
        stats = StrategyStats()
        stats.record_request_start()
        stats.record_request_end(success=True, response_time_ms=100.0)
        stats.record_request_start()
        stats.record_request_end(success=True, response_time_ms=200.0)
        assert stats.average_response_time == pytest.approx(150.0)

    def test_reset_stats_clears_everything(self):
        stats = StrategyStats()
        stats.record_request_start()
        stats.record_request_end(success=False, response_time_ms=50.0)
        stats.is_healthy = False
        stats.reset_stats()
        assert stats.total_requests == 0
        assert stats.successful_requests == 0
        assert stats.failed_requests == 0
        assert stats.consecutive_failures == 0
        assert stats.active_connections == 0
        assert stats.is_healthy is True
        assert stats.average_response_time == 0.0

    def test_success_rate_calculation(self):
        stats = StrategyStats()
        for _ in range(3):
            stats.record_request_start()
            stats.record_request_end(success=True, response_time_ms=10.0)
        stats.record_request_start()
        stats.record_request_end(success=False, response_time_ms=10.0)
        assert stats.success_rate == pytest.approx(75.0)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingStrategyConstruction:
    def test_rejects_empty_strategies_list(self, mock_logger):
        with pytest.raises(ValueError, match="At least one strategy"):
            LoadBalancingProviderStrategy(mock_logger, [])

    def test_provider_type_reflects_strategies(self, mock_logger):
        s_a = ConcreteProviderStrategy("lb_a")
        s_b = ConcreteProviderStrategy("lb_b")
        lb = LoadBalancingProviderStrategy(mock_logger, [s_a, s_b])
        assert "lb_a" in lb.provider_type
        assert "lb_b" in lb.provider_type

    def test_custom_weights_are_stored(self, mock_logger):
        s = ConcreteProviderStrategy("weighted")
        lb = LoadBalancingProviderStrategy(mock_logger, [s], weights={"weighted": 2.5})
        assert lb._stats["weighted"].weight == pytest.approx(2.5)

    def test_default_weight_is_one(self, mock_logger):
        s = ConcreteProviderStrategy("def_w")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        assert lb._stats["def_w"].weight == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingStrategyInitialization:
    def test_initialize_succeeds_when_strategy_initializes(self, mock_logger):
        s = ConcreteProviderStrategy("init_lb")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        assert lb.initialize() is True
        assert lb.is_initialized

    def test_initialize_idempotent(self, mock_logger):
        # Spy that always reports as uninitialised, so the only thing
        # preventing re-initialisation on the second call is the strategy's
        # own idempotency guard (`if self._initialized: return True`).
        spy = MagicMock()
        spy.provider_type = "idem_lb"
        spy.is_initialized = False
        spy.initialize.return_value = True
        lb = LoadBalancingProviderStrategy(mock_logger, [spy])
        lb.initialize()
        result = lb.initialize()
        assert result is True
        spy.initialize.assert_called_once()

    def test_initialize_fails_when_no_strategy_initializes(self, mock_logger):
        broken = MagicMock()
        broken.provider_type = "broken_lb"
        broken.is_initialized = False
        broken.initialize.return_value = False
        lb = LoadBalancingProviderStrategy(mock_logger, [broken])
        result = lb.initialize()
        assert result is False


# ---------------------------------------------------------------------------
# execute_operation — general
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingExecuteOperation:
    def test_execute_succeeds_on_healthy_strategy(self, mock_logger):
        s = ConcreteProviderStrategy(
            "exec_lb", operation_result=ProviderResult.success_result({"lb": True})
        )
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        result = asyncio.run(lb.execute_operation(make_op()))
        assert result.success

    def test_execute_returns_error_when_strategy_raises(self, mock_logger):
        s = ConcreteProviderStrategy("exc_lb", operation_raises=RuntimeError("lb_error"))
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        result = asyncio.run(lb.execute_operation(make_op()))
        assert not result.success
        assert result.error_message is not None and "lb_error" in result.error_message

    def test_records_stats_on_success(self, mock_logger):
        s = ConcreteProviderStrategy("stats_lb", operation_result=ProviderResult.success_result({}))
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        asyncio.run(lb.execute_operation(make_op()))
        stats = lb.get_stats()["stats_lb"]
        assert stats["total_requests"] == 1
        assert stats["successful_requests"] == 1

    def test_records_stats_on_failure(self, mock_logger):
        s = ConcreteProviderStrategy(
            "fail_stats_lb",
            operation_result=ProviderResult.error_result("boom", "ERR"),
        )
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        asyncio.run(lb.execute_operation(make_op()))
        stats = lb.get_stats()["fail_stats_lb"]
        assert stats["failed_requests"] == 1


# ---------------------------------------------------------------------------
# Round-robin selection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingRoundRobin:
    def test_round_robin_cycles_through_strategies(self, mock_logger):
        s_a = ConcreteProviderStrategy("rr_a", operation_result=ProviderResult.success_result({}))
        s_b = ConcreteProviderStrategy("rr_b", operation_result=ProviderResult.success_result({}))
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.ROUND_ROBIN)
        lb = LoadBalancingProviderStrategy(mock_logger, [s_a, s_b], config=cfg)
        lb.initialize()
        for _ in range(4):
            asyncio.run(lb.execute_operation(make_op()))
        stats = lb.get_stats()
        # Both should have been called (order may vary with dict ordering)
        assert stats["rr_a"]["total_requests"] > 0
        assert stats["rr_b"]["total_requests"] > 0

    def test_round_robin_increments_index(self, mock_logger):
        s = ConcreteProviderStrategy("rr_idx", operation_result=ProviderResult.success_result({}))
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.ROUND_ROBIN)
        lb = LoadBalancingProviderStrategy(mock_logger, [s], config=cfg)
        lb.initialize()
        initial_index = lb._round_robin_index
        asyncio.run(lb.execute_operation(make_op()))
        assert lb._round_robin_index == initial_index + 1


# ---------------------------------------------------------------------------
# Least connections selection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingLeastConnections:
    def test_selects_strategy_with_fewest_connections(self, mock_logger):
        s_busy = ConcreteProviderStrategy(
            "lc_busy", operation_result=ProviderResult.success_result({})
        )
        s_free = ConcreteProviderStrategy(
            "lc_free", operation_result=ProviderResult.success_result({})
        )
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.LEAST_CONNECTIONS)
        lb = LoadBalancingProviderStrategy(mock_logger, [s_busy, s_free], config=cfg)
        lb.initialize()
        # Artificially inflate active connections for s_busy
        lb._stats["lc_busy"].active_connections = 5
        lb._stats["lc_free"].active_connections = 0
        selected = lb._least_connections_selection(lb._strategies)
        assert selected.provider_type == "lc_free"


# ---------------------------------------------------------------------------
# Least response time selection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingLeastResponseTime:
    def test_selects_fastest_strategy(self, mock_logger):
        s_slow = ConcreteProviderStrategy(
            "lrt_slow", operation_result=ProviderResult.success_result({})
        )
        s_fast = ConcreteProviderStrategy(
            "lrt_fast", operation_result=ProviderResult.success_result({})
        )
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.LEAST_RESPONSE_TIME)
        lb = LoadBalancingProviderStrategy(mock_logger, [s_slow, s_fast], config=cfg)
        lb.initialize()
        lb._stats["lrt_slow"].average_response_time = 200.0
        lb._stats["lrt_fast"].average_response_time = 10.0
        selected = lb._least_response_time_selection(lb._strategies)
        assert selected.provider_type == "lrt_fast"


# ---------------------------------------------------------------------------
# Hash-based selection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingHashBased:
    def test_hash_based_is_consistent_for_same_operation(self, mock_logger):
        s_a = ConcreteProviderStrategy("hb_a", operation_result=ProviderResult.success_result({}))
        s_b = ConcreteProviderStrategy("hb_b", operation_result=ProviderResult.success_result({}))
        cfg = LoadBalancingConfig(algorithm=LoadBalancingAlgorithm.HASH_BASED)
        lb = LoadBalancingProviderStrategy(mock_logger, [s_a, s_b], config=cfg)
        lb.initialize()
        op = make_op(params={"key": "stable"})
        selected_1 = lb._hash_based_selection(lb._strategies, op)
        selected_2 = lb._hash_based_selection(lb._strategies, op)
        assert selected_1.provider_type == selected_2.provider_type


# ---------------------------------------------------------------------------
# Random selection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingRandom:
    def test_random_selection_returns_a_strategy(self, mock_logger):
        s = ConcreteProviderStrategy("rand_s")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        selected = lb._random_selection(lb._strategies)
        assert selected.provider_type == "rand_s"


# ---------------------------------------------------------------------------
# Health status update
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingHealthStatusUpdate:
    def test_strategy_marked_unhealthy_after_consecutive_failures(self, mock_logger):
        # _update_health_status checks consecutive_failures >= threshold
        # so we need the counter to already be AT the threshold before calling it
        s = ConcreteProviderStrategy("health_s")
        cfg = LoadBalancingConfig(unhealthy_threshold=2)
        lb = LoadBalancingProviderStrategy(mock_logger, [s], config=cfg)
        lb.initialize()
        lb._stats["health_s"].consecutive_failures = 2  # at threshold
        lb._update_health_status("health_s", False)
        assert lb._stats["health_s"].is_healthy is False

    def test_strategy_marked_healthy_after_consecutive_successes(self, mock_logger):
        # _update_health_status checks consecutive_successes >= threshold
        s = ConcreteProviderStrategy("recover_s")
        cfg = LoadBalancingConfig(recovery_threshold=2)
        lb = LoadBalancingProviderStrategy(mock_logger, [s], config=cfg)
        lb.initialize()
        lb._stats["recover_s"].is_healthy = False
        lb._stats["recover_s"].consecutive_successes = 2  # at threshold
        lb._update_health_status("recover_s", True)
        assert lb._stats["recover_s"].is_healthy is True

    def test_unhealthy_strategy_used_as_fallback_when_all_unhealthy(self, mock_logger):
        """When no healthy strategies exist, any available strategy is selected."""
        s = ConcreteProviderStrategy("all_bad", operation_result=ProviderResult.success_result({}))
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        lb._stats["all_bad"].is_healthy = False
        # Should still succeed (uses all strategies as fallback)
        result = asyncio.run(lb.execute_operation(make_op()))
        assert result.success


# ---------------------------------------------------------------------------
# get_health_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingGetHealthStatus:
    def test_healthy_when_all_strategies_healthy(self, mock_logger):
        s = ConcreteProviderStrategy("ghs_s")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        status = lb.get_health_status()
        assert status.is_healthy

    def test_unhealthy_when_no_strategies_healthy(self, mock_logger):
        s = ConcreteProviderStrategy("ghs_bad_s")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        lb._stats["ghs_bad_s"].is_healthy = False
        status = lb.get_health_status()
        assert not status.is_healthy

    def test_degraded_message_when_partial_healthy(self, mock_logger):
        s_a = ConcreteProviderStrategy("ghs_a")
        s_b = ConcreteProviderStrategy("ghs_b")
        lb = LoadBalancingProviderStrategy(mock_logger, [s_a, s_b])
        lb.initialize()
        lb._stats["ghs_a"].is_healthy = False
        status = lb.get_health_status()
        assert status.is_healthy  # 1/2 still healthy
        assert "degraded" in status.status_message


# ---------------------------------------------------------------------------
# get_capabilities
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingGetCapabilities:
    def test_capabilities_deduplicate_operations(self, mock_logger):
        # Both strategies support all ops — union should be same as all ops
        s_a = ConcreteProviderStrategy("caps_a")
        s_b = ConcreteProviderStrategy("caps_b")
        lb = LoadBalancingProviderStrategy(mock_logger, [s_a, s_b])
        lb.initialize()
        caps = lb.get_capabilities()
        # Check there are no duplicates
        assert len(caps.supported_operations) == len(set(caps.supported_operations))

    def test_capabilities_provider_type_set(self, mock_logger):
        s = ConcreteProviderStrategy("caps_s")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        caps = lb.get_capabilities()
        assert "caps_s" in caps.provider_type


# ---------------------------------------------------------------------------
# get_stats / reset_stats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingStats:
    def test_get_stats_returns_dict_per_strategy(self, mock_logger):
        s_a = ConcreteProviderStrategy("st_a")
        s_b = ConcreteProviderStrategy("st_b")
        lb = LoadBalancingProviderStrategy(mock_logger, [s_a, s_b])
        lb.initialize()
        stats = lb.get_stats()
        assert "st_a" in stats
        assert "st_b" in stats

    def test_get_stats_keys_match_expected_fields(self, mock_logger):
        s = ConcreteProviderStrategy("st_s")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        stats = lb.get_stats()["st_s"]
        for field in ["active_connections", "total_requests", "success_rate", "is_healthy"]:
            assert field in stats

    def test_reset_stats_clears_counters(self, mock_logger):
        s = ConcreteProviderStrategy("rst_s", operation_result=ProviderResult.success_result({}))
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        asyncio.run(lb.execute_operation(make_op()))
        lb.reset_stats()
        assert lb.get_stats()["rst_s"]["total_requests"] == 0


# ---------------------------------------------------------------------------
# Sticky sessions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingStickySessions:
    def test_sticky_session_routes_to_same_strategy(self, mock_logger):
        s_a = ConcreteProviderStrategy(
            "sticky_a", operation_result=ProviderResult.success_result({})
        )
        s_b = ConcreteProviderStrategy(
            "sticky_b", operation_result=ProviderResult.success_result({})
        )
        cfg = LoadBalancingConfig(
            sticky_sessions=True,
            algorithm=LoadBalancingAlgorithm.ROUND_ROBIN,
        )
        lb = LoadBalancingProviderStrategy(mock_logger, [s_a, s_b], config=cfg)
        lb.initialize()
        # Manually assign session
        lb._sessions["sess-1"] = "sticky_a"
        lb._session_timestamps["sess-1"] = __import__("time").time()

        class StickyOp(ProviderOperation):
            pass

        op = StickyOp(
            operation_type=ProviderOperationType.HEALTH_CHECK,
            parameters={},
        )
        op.session_id = "sess-1"  # type: ignore[attr-defined]
        selected = lb._select_strategy(op)
        assert selected is not None
        assert selected.provider_type == "sticky_a"

    def test_expired_session_removed_on_get(self, mock_logger):
        s = ConcreteProviderStrategy("exp_s")
        cfg = LoadBalancingConfig(sticky_sessions=True, session_timeout_seconds=0.001)
        lb = LoadBalancingProviderStrategy(mock_logger, [s], config=cfg)
        lb.initialize()
        lb._sessions["expired"] = "exp_s"
        lb._session_timestamps["expired"] = 0.0  # very old
        result = lb._get_session_strategy("expired")
        assert result is None  # expired and removed
        assert "expired" not in lb._sessions


# ---------------------------------------------------------------------------
# cleanup / shutdown
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingCleanup:
    def test_cleanup_marks_not_initialized(self, mock_logger):
        s = ConcreteProviderStrategy("cleanup_lb")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        lb.cleanup()
        assert not lb.is_initialized

    def test_cleanup_calls_strategy_cleanup(self, mock_logger):
        s = ConcreteProviderStrategy("cln_lb_s")
        s.initialize()
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.initialize()
        lb.cleanup()
        assert not s._initialized

    def test_cleanup_tolerates_strategy_exception(self, mock_logger):
        broken = MagicMock()
        broken.provider_type = "broken_cleanup_lb"
        broken.cleanup.side_effect = RuntimeError("cannot clean")
        lb = LoadBalancingProviderStrategy(mock_logger, [broken])
        lb.initialize()
        lb.cleanup()  # must not raise
        mock_logger.warning.assert_called()

    def test_shutdown_sets_event(self, mock_logger):
        s = ConcreteProviderStrategy("shutdown_s")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        lb.shutdown()
        assert lb._shutdown_event.is_set()


# ---------------------------------------------------------------------------
# String representations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBalancingRepresentations:
    def test_generate_provider_name_returns_provider_type(self, mock_logger):
        s = ConcreteProviderStrategy("gn_lb")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        assert lb.generate_provider_name({}) == lb.provider_type

    def test_parse_provider_name_returns_dict(self, mock_logger):
        s = ConcreteProviderStrategy("parse_lb")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        assert lb.parse_provider_name("x")["provider_type"] == "x"

    def test_get_provider_name_pattern_returns_load_balancer(self, mock_logger):
        s = ConcreteProviderStrategy("pnp_lb")
        lb = LoadBalancingProviderStrategy(mock_logger, [s])
        assert lb.get_provider_name_pattern() == "load_balancer"

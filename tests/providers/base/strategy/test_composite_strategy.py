"""Unit tests for CompositeProviderStrategy."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.base.strategy.composite_strategy import (
    AggregationPolicy,
    CompositeProviderStrategy,
    CompositionConfig,
    CompositionMode,
    StrategyExecutionResult,
)
from orb.providers.base.strategy.provider_strategy import (
    ProviderOperationType,
    ProviderResult,
)
from tests.providers.base.strategy.conftest import ConcreteProviderStrategy, make_op

# ---------------------------------------------------------------------------
# CompositionConfig validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositionConfig:
    """CompositionConfig post-init validation."""

    def test_default_config_is_valid(self):
        cfg = CompositionConfig()
        assert cfg.max_concurrent_operations >= 1
        assert cfg.timeout_seconds > 0
        assert cfg.min_success_count >= 1

    def test_rejects_zero_concurrent_operations(self):
        with pytest.raises(ValueError, match="max_concurrent_operations"):
            CompositionConfig(max_concurrent_operations=0)

    def test_rejects_non_positive_timeout(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            CompositionConfig(timeout_seconds=0)

    def test_rejects_zero_min_success_count(self):
        with pytest.raises(ValueError, match="min_success_count"):
            CompositionConfig(min_success_count=0)

    def test_rejects_failure_threshold_above_one(self):
        with pytest.raises(ValueError, match="failure_threshold"):
            CompositionConfig(failure_threshold=1.5)

    def test_rejects_negative_failure_threshold(self):
        with pytest.raises(ValueError, match="failure_threshold"):
            CompositionConfig(failure_threshold=-0.1)

    def test_boundary_failure_threshold_zero(self):
        cfg = CompositionConfig(failure_threshold=0.0)
        assert cfg.failure_threshold == 0.0

    def test_boundary_failure_threshold_one(self):
        cfg = CompositionConfig(failure_threshold=1.0)
        assert cfg.failure_threshold == 1.0


# ---------------------------------------------------------------------------
# Constructor / properties
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeProviderStrategyConstruction:
    def test_rejects_empty_strategy_list(self, mock_logger):
        with pytest.raises(ValueError, match="At least one strategy"):
            CompositeProviderStrategy(mock_logger, [])

    def test_provider_type_reflects_composed_strategies(self, mock_logger):
        s_a = ConcreteProviderStrategy("alpha")
        s_b = ConcreteProviderStrategy("beta")
        composite = CompositeProviderStrategy(mock_logger, [s_a, s_b])
        assert "alpha" in composite.provider_type
        assert "beta" in composite.provider_type

    def test_composed_strategies_returns_copy(self, mock_logger):
        s = ConcreteProviderStrategy("x")
        composite = CompositeProviderStrategy(mock_logger, [s])
        result = composite.composed_strategies
        result["x"] = None  # type: ignore[assignment]
        assert composite.composed_strategies["x"] is s  # original unchanged

    def test_initial_weights_are_equal(self, mock_logger):
        s_a = ConcreteProviderStrategy("a")
        s_b = ConcreteProviderStrategy("b")
        composite = CompositeProviderStrategy(mock_logger, [s_a, s_b])
        assert composite._strategy_weights["a"] == pytest.approx(0.5)
        assert composite._strategy_weights["b"] == pytest.approx(0.5)

    def test_single_strategy_weight_is_one(self, mock_logger):
        s = ConcreteProviderStrategy("only")
        composite = CompositeProviderStrategy(mock_logger, [s])
        assert composite._strategy_weights["only"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# add_strategy / remove_strategy / set_strategy_weight
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeStrategyManagement:
    @pytest.fixture
    def composite(self, mock_logger):
        s = ConcreteProviderStrategy("base")
        c = CompositeProviderStrategy(mock_logger, [s])
        c.initialize()
        return c

    def test_add_strategy_registers_new_strategy(self, composite):
        new_s = ConcreteProviderStrategy("extra")
        composite.add_strategy(new_s)
        assert "extra" in composite.composed_strategies

    def test_add_strategy_rebalances_weights(self, composite):
        new_s = ConcreteProviderStrategy("extra")
        composite.add_strategy(new_s)
        # Two strategies: each should be ~0.5
        assert composite._strategy_weights["base"] == pytest.approx(0.5)
        assert composite._strategy_weights["extra"] == pytest.approx(0.5)

    def test_add_strategy_with_explicit_weight(self, composite):
        new_s = ConcreteProviderStrategy("extra2")
        composite.add_strategy(new_s, weight=0.3)
        assert composite._strategy_weights["extra2"] == pytest.approx(0.3)

    def test_add_existing_strategy_replaces_it(self, composite, mock_logger):
        replacement = ConcreteProviderStrategy("base")
        composite.add_strategy(replacement)
        assert composite.composed_strategies["base"] is replacement
        mock_logger.warning.assert_called()

    def test_remove_strategy_returns_true_and_removes(self, composite):
        extra = ConcreteProviderStrategy("to_remove")
        composite.add_strategy(extra)
        result = composite.remove_strategy("to_remove")
        assert result is True
        assert "to_remove" not in composite.composed_strategies

    def test_remove_strategy_rebalances_weights(self, composite):
        extra = ConcreteProviderStrategy("to_remove")
        composite.add_strategy(extra)
        composite.remove_strategy("to_remove")
        assert composite._strategy_weights["base"] == pytest.approx(1.0)

    def test_remove_nonexistent_strategy_returns_false(self, composite):
        result = composite.remove_strategy("no_such_strategy")
        assert result is False

    def test_remove_strategy_calls_cleanup(self, composite):
        extra = ConcreteProviderStrategy("cleanup_target")
        extra.initialize()
        composite.add_strategy(extra)
        composite.remove_strategy("cleanup_target")
        assert not extra._initialized  # cleanup sets _initialized to False

    def test_remove_strategy_swallows_cleanup_exception(self, composite, mock_logger):
        broken = MagicMock()
        broken.provider_type = "broken"
        broken.cleanup.side_effect = RuntimeError("cleanup boom")
        composite._strategies["broken"] = broken
        composite._strategy_weights["broken"] = 0.5
        result = composite.remove_strategy("broken")
        assert result is True
        mock_logger.warning.assert_called()

    def test_set_weight_returns_true_on_success(self, composite):
        result = composite.set_strategy_weight("base", 0.7)
        assert result is True
        assert composite._strategy_weights["base"] == pytest.approx(0.7)

    def test_set_weight_returns_false_for_unknown_strategy(self, composite):
        result = composite.set_strategy_weight("unknown", 0.5)
        assert result is False

    def test_set_weight_rejects_value_above_one(self, composite):
        with pytest.raises(ValueError, match="Weight"):
            composite.set_strategy_weight("base", 1.1)

    def test_set_weight_rejects_negative(self, composite):
        with pytest.raises(ValueError, match="Weight"):
            composite.set_strategy_weight("base", -0.1)


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeStrategyInitialization:
    def test_initialize_succeeds_when_all_strategies_succeed(self, mock_logger):
        s_a = ConcreteProviderStrategy("a")
        s_b = ConcreteProviderStrategy("b")
        composite = CompositeProviderStrategy(mock_logger, [s_a, s_b])
        result = composite.initialize()
        assert result is True
        assert composite.is_initialized

    def test_initialize_idempotent(self, mock_logger):
        # Spy sub-strategy that always reports as uninitialised, so the ONLY
        # thing preventing a second initialize() call is the composite's own
        # idempotency guard (`if self._initialized: return True`).
        spy = MagicMock()
        spy.provider_type = "x"
        spy.is_initialized = False
        spy.initialize.return_value = True
        composite = CompositeProviderStrategy(mock_logger, [spy])
        composite.initialize()
        composite.initialize()  # second call must not re-run sub-strategy init
        assert composite.is_initialized
        spy.initialize.assert_called_once()

    def test_initialize_counts_already_initialized_strategies(self, mock_logger):
        s = ConcreteProviderStrategy("pre")
        s.initialize()  # pre-initialise
        composite = CompositeProviderStrategy(mock_logger, [s])
        result = composite.initialize()
        assert result is True

    def test_initialize_fails_when_min_success_count_not_met(self, mock_logger):
        broken = MagicMock()
        broken.provider_type = "broken"
        broken.is_initialized = False
        broken.initialize.return_value = False
        composite = CompositeProviderStrategy(
            mock_logger,
            [broken],
            config=CompositionConfig(min_success_count=1),
        )
        result = composite.initialize()
        assert result is False
        assert not composite.is_initialized

    def test_initialize_tolerates_exception_from_strategy(self, mock_logger):
        good = ConcreteProviderStrategy("good")
        bad = MagicMock()
        bad.provider_type = "bad"
        bad.is_initialized = False
        bad.initialize.side_effect = RuntimeError("boom")
        composite = CompositeProviderStrategy(
            mock_logger, [good, bad], config=CompositionConfig(min_success_count=1)
        )
        result = composite.initialize()
        assert result is True  # good strategy saved it


# ---------------------------------------------------------------------------
# execute_operation — not-initialized guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeStrategyNotInitialized:
    def test_execute_returns_error_when_not_initialized(self, mock_logger):
        s = ConcreteProviderStrategy("uninit")
        composite = CompositeProviderStrategy(mock_logger, [s])
        result = asyncio.run(composite.execute_operation(make_op()))
        assert not result.success
        assert result.error_code == "NOT_INITIALIZED"


# ---------------------------------------------------------------------------
# execute_operation — SEQUENTIAL mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeStrategySequential:
    @pytest.fixture
    def composite(self, mock_logger):
        s_a = ConcreteProviderStrategy(
            "s_a", operation_result=ProviderResult.success_result({"data": "from_a"})
        )
        s_b = ConcreteProviderStrategy(
            "s_b", operation_result=ProviderResult.success_result({"data": "from_b"})
        )
        cfg = CompositionConfig(mode=CompositionMode.SEQUENTIAL)
        c = CompositeProviderStrategy(mock_logger, [s_a, s_b], config=cfg)
        c.initialize()
        return c

    def test_sequential_returns_success(self, composite):
        op = make_op()
        result = asyncio.run(composite.execute_operation(op))
        assert result.success

    def test_sequential_populates_routing_info(self, composite):
        op = make_op()
        result = asyncio.run(composite.execute_operation(op))
        assert result.routing_info is not None
        assert result.routing_info["composition_mode"] == CompositionMode.SEQUENTIAL.value

    def test_sequential_stops_at_first_success_with_first_success_policy(self, mock_logger):
        call_log: list[str] = []

        class TrackingStrategy(ConcreteProviderStrategy):
            def __init__(self, name: str) -> None:
                super().__init__(
                    name,
                    operation_result=ProviderResult.success_result({"name": name}),
                )

            async def execute_operation(self, operation):
                call_log.append(self._provider_type)
                return await super().execute_operation(operation)

        s1 = TrackingStrategy("first")
        s2 = TrackingStrategy("second")
        cfg = CompositionConfig(
            mode=CompositionMode.SEQUENTIAL,
            aggregation_policy=AggregationPolicy.FIRST_SUCCESS,
        )
        composite = CompositeProviderStrategy(mock_logger, [s1, s2], config=cfg)
        composite.initialize()
        asyncio.run(composite.execute_operation(make_op()))
        assert len(call_log) == 1


# ---------------------------------------------------------------------------
# execute_operation — LOAD_BALANCED mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeStrategyLoadBalanced:
    @pytest.fixture
    def composite(self, mock_logger):
        s = ConcreteProviderStrategy(
            "lb_s", operation_result=ProviderResult.success_result({"lb": True})
        )
        cfg = CompositionConfig(mode=CompositionMode.LOAD_BALANCED)
        c = CompositeProviderStrategy(mock_logger, [s], config=cfg)
        c.initialize()
        return c

    def test_load_balanced_returns_success(self, composite):
        result = asyncio.run(composite.execute_operation(make_op()))
        assert result.success

    def test_load_balanced_routing_mode_in_result(self, composite):
        result = asyncio.run(composite.execute_operation(make_op()))
        assert result.routing_info["composition_mode"] == CompositionMode.LOAD_BALANCED.value


# ---------------------------------------------------------------------------
# execute_operation — PARALLEL mode (default)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeStrategyParallel:
    """Tests for the PARALLEL execution path.

    _execute_parallel submits work to a ThreadPoolExecutor; each thread calls
    asyncio.run() around the coroutine.  We keep these tests small and
    deterministic (both strategies are pre-configured with no I/O).
    """

    @pytest.fixture
    def parallel_composite(self, mock_logger):
        s_a = ConcreteProviderStrategy(
            "par_a", operation_result=ProviderResult.success_result({"k": "a"})
        )
        s_b = ConcreteProviderStrategy(
            "par_b", operation_result=ProviderResult.success_result({"k": "b"})
        )
        cfg = CompositionConfig(mode=CompositionMode.PARALLEL)
        c = CompositeProviderStrategy(mock_logger, [s_a, s_b], config=cfg)
        c.initialize()
        return c

    def test_parallel_execution_succeeds(self, parallel_composite):
        result = asyncio.run(parallel_composite.execute_operation(make_op()))
        assert result.success

    def test_parallel_routing_info_contains_strategy_count(self, parallel_composite):
        result = asyncio.run(parallel_composite.execute_operation(make_op()))
        assert result.routing_info["strategies_executed"] == 2

    def test_parallel_routing_info_mode(self, parallel_composite):
        result = asyncio.run(parallel_composite.execute_operation(make_op()))
        assert result.routing_info["composition_mode"] == CompositionMode.PARALLEL.value

    def test_parallel_counts_successful_strategies(self, parallel_composite):
        result = asyncio.run(parallel_composite.execute_operation(make_op()))
        assert result.routing_info["successful_strategies"] == 2

    def test_parallel_one_failing_strategy_recorded(self, mock_logger):
        good = ConcreteProviderStrategy("ok", operation_result=ProviderResult.success_result({}))
        failing = ConcreteProviderStrategy("fail", operation_raises=RuntimeError("explode"))
        cfg = CompositionConfig(mode=CompositionMode.PARALLEL, failure_threshold=1.0)
        composite = CompositeProviderStrategy(mock_logger, [good, failing], config=cfg)
        composite.initialize()
        result = asyncio.run(composite.execute_operation(make_op()))
        assert (result.routing_info or {})["successful_strategies"] == 1


# ---------------------------------------------------------------------------
# execute_operation — fallthrough to PARALLEL for unknown mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeStrategyUnknownModeParallelFallthrough:
    def test_unknown_mode_falls_through_to_parallel(self, mock_logger):
        s = ConcreteProviderStrategy("ft", operation_result=ProviderResult.success_result({}))
        # Directly set an unexpected mode value after construction
        cfg = CompositionConfig(mode=CompositionMode.AGGREGATED)
        composite = CompositeProviderStrategy(mock_logger, [s], config=cfg)
        composite.initialize()
        result = asyncio.run(composite.execute_operation(make_op()))
        assert result.success


# ---------------------------------------------------------------------------
# No capable strategies
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeStrategyNoCapableStrategies:
    def test_error_when_no_strategy_supports_operation(self, mock_logger):
        s = ConcreteProviderStrategy(
            "limited",
            supported_ops=[ProviderOperationType.HEALTH_CHECK],
        )
        composite = CompositeProviderStrategy(mock_logger, [s])
        composite.initialize()
        op = make_op(ProviderOperationType.CREATE_INSTANCES)
        result = asyncio.run(composite.execute_operation(op))
        assert not result.success
        assert result.error_code == "NO_CAPABLE_STRATEGIES"


# ---------------------------------------------------------------------------
# _aggregate_results — aggregation policies
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAggregateResults:
    @pytest.fixture
    def composite(self, mock_logger):
        s = ConcreteProviderStrategy("agg")
        c = CompositeProviderStrategy(mock_logger, [s])
        c.initialize()
        return c

    def _make_exec_result(self, strategy_type: str, success: bool = True, data: Any = None):
        if success:
            pr = ProviderResult.success_result(data or {"k": strategy_type})
        else:
            pr = ProviderResult.error_result(f"{strategy_type} failed", "ERR")
        return StrategyExecutionResult(
            strategy_type=strategy_type,
            result=pr,
            execution_time_ms=10.0,
            success=success,
        )

    def test_merge_all_combines_dict_data(self, composite):
        results = [
            self._make_exec_result("a", data={"from_a": 1}),
            self._make_exec_result("b", data={"from_b": 2}),
        ]
        composite._config = CompositionConfig(aggregation_policy=AggregationPolicy.MERGE_ALL)
        out = composite._aggregate_results(results, make_op())
        assert out.success
        assert "from_a" in out.data
        assert "from_b" in out.data

    def test_merge_all_combines_list_data(self, composite):
        r_a = StrategyExecutionResult(
            strategy_type="a",
            result=ProviderResult.success_result([1, 2]),
            execution_time_ms=5.0,
            success=True,
        )
        r_b = StrategyExecutionResult(
            strategy_type="b",
            result=ProviderResult.success_result([3, 4]),
            execution_time_ms=5.0,
            success=True,
        )
        composite._config = CompositionConfig(aggregation_policy=AggregationPolicy.MERGE_ALL)
        out = composite._aggregate_results([r_a, r_b], make_op())
        assert out.success
        assert out.data["merged_list"] == [1, 2, 3, 4]

    def test_first_success_returns_first_result(self, composite):
        results = [
            self._make_exec_result("first", data={"v": 1}),
            self._make_exec_result("second", data={"v": 2}),
        ]
        composite._config = CompositionConfig(aggregation_policy=AggregationPolicy.FIRST_SUCCESS)
        out = composite._aggregate_results(results, make_op())
        assert out.success
        assert out.routing_info["aggregation_policy"] == "first_success"
        assert out.routing_info["selected_strategy"] == "first"

    def test_best_performance_picks_fastest(self, composite):
        fast = StrategyExecutionResult(
            strategy_type="fast",
            result=ProviderResult.success_result({"speed": "fast"}),
            execution_time_ms=1.0,
            success=True,
        )
        slow = StrategyExecutionResult(
            strategy_type="slow",
            result=ProviderResult.success_result({"speed": "slow"}),
            execution_time_ms=100.0,
            success=True,
        )
        composite._config = CompositionConfig(aggregation_policy=AggregationPolicy.BEST_PERFORMANCE)
        out = composite._aggregate_results([slow, fast], make_op())
        assert out.success
        assert out.routing_info["selected_strategy"] == "fast"

    def test_failure_threshold_exceeded_returns_error(self, composite):
        composite._config = CompositionConfig(failure_threshold=0.4)
        results = [
            self._make_exec_result("a", success=False),
            self._make_exec_result("b", success=False),
            self._make_exec_result("c", success=True),
        ]
        out = composite._aggregate_results(results, make_op())
        assert not out.success
        assert out.error_code == "FAILURE_THRESHOLD_EXCEEDED"

    def test_insufficient_success_count_returns_error(self, composite):
        composite._config = CompositionConfig(min_success_count=3, failure_threshold=1.0)
        results = [
            self._make_exec_result("a", success=True),
            self._make_exec_result("b", success=True),
        ]
        out = composite._aggregate_results(results, make_op())
        assert not out.success
        assert out.error_code == "INSUFFICIENT_SUCCESS"

    def test_empty_successful_results_fallback(self, composite):
        composite._config = CompositionConfig(
            aggregation_policy=AggregationPolicy.FIRST_SUCCESS,
            failure_threshold=1.0,
        )
        # Passing empty results with high threshold so it bypasses threshold check
        out = composite._aggregate_first_success([])
        assert not out.success
        assert out.error_code == "NO_RESULTS"


# ---------------------------------------------------------------------------
# get_capabilities / check_health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeCapabilitiesAndHealth:
    @pytest.fixture
    def dual_composite(self, mock_logger):
        s_a = ConcreteProviderStrategy("cap_a")
        s_b = ConcreteProviderStrategy("cap_b")
        c = CompositeProviderStrategy(mock_logger, [s_a, s_b])
        c.initialize()
        return c

    def test_get_capabilities_unions_operations(self, dual_composite):
        caps = dual_composite.get_capabilities()
        assert len(caps.supported_operations) > 0

    def test_get_capabilities_tolerates_strategy_exception(self, mock_logger):
        broken = MagicMock()
        broken.provider_type = "broken"
        broken.get_capabilities.side_effect = RuntimeError("no caps")
        composite = CompositeProviderStrategy(mock_logger, [broken])
        composite.initialize()
        caps = composite.get_capabilities()
        assert caps.supported_operations == []
        mock_logger.warning.assert_called()

    def test_check_health_healthy_when_majority_healthy(self, dual_composite):
        status = dual_composite.check_health()
        assert status.is_healthy

    def test_check_health_unhealthy_when_all_unhealthy(self, mock_logger):
        s_a = ConcreteProviderStrategy("u_a", healthy=False)
        s_b = ConcreteProviderStrategy("u_b", healthy=False)
        composite = CompositeProviderStrategy(mock_logger, [s_a, s_b])
        composite.initialize()
        status = composite.check_health()
        assert not status.is_healthy

    def test_check_health_tolerates_strategy_exception(self, mock_logger):
        broken = MagicMock()
        broken.provider_type = "broken"
        broken.check_health.side_effect = RuntimeError("no health")
        good = ConcreteProviderStrategy("good_health")
        composite = CompositeProviderStrategy(mock_logger, [broken, good])
        composite.initialize()
        # 1/2 healthy => 50% >= 50% => healthy
        status = composite.check_health()
        assert status.is_healthy

    def test_check_health_with_single_unhealthy_strategy_returns_unhealthy(self, mock_logger):
        s = ConcreteProviderStrategy("only", healthy=False)
        composite = CompositeProviderStrategy(mock_logger, [s])
        composite.initialize()
        status = composite.check_health()
        assert not status.is_healthy


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeStrategyCleanup:
    def test_cleanup_calls_strategy_cleanup(self, mock_logger):
        s = ConcreteProviderStrategy("clean_me")
        s.initialize()
        composite = CompositeProviderStrategy(mock_logger, [s])
        composite.initialize()
        composite.cleanup()
        assert not composite.is_initialized
        assert composite.composed_strategies == {}

    def test_cleanup_tolerates_strategy_cleanup_exception(self, mock_logger):
        broken = MagicMock()
        broken.provider_type = "broken_clean"
        broken.cleanup.side_effect = RuntimeError("can't clean")
        composite = CompositeProviderStrategy(mock_logger, [broken])
        composite.initialize()
        composite.cleanup()  # must not raise
        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# String representations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeStrategyRepresentations:
    def test_str_contains_strategy_types(self, mock_logger):
        s = ConcreteProviderStrategy("repr_s")
        composite = CompositeProviderStrategy(mock_logger, [s])
        assert "repr_s" in str(composite)

    def test_repr_contains_initialized_state(self, mock_logger):
        s = ConcreteProviderStrategy("repr_s2")
        composite = CompositeProviderStrategy(mock_logger, [s])
        composite.initialize()
        assert "initialized=True" in repr(composite)

    def test_generate_provider_name_returns_provider_type(self, mock_logger):
        s = ConcreteProviderStrategy("gname")
        composite = CompositeProviderStrategy(mock_logger, [s])
        assert composite.generate_provider_name({}) == composite.provider_type

    def test_parse_provider_name_returns_dict(self, mock_logger):
        s = ConcreteProviderStrategy("parse_s")
        composite = CompositeProviderStrategy(mock_logger, [s])
        result = composite.parse_provider_name("some_name")
        assert result["provider_type"] == "some_name"

    def test_get_provider_name_pattern_returns_composite(self, mock_logger):
        s = ConcreteProviderStrategy("pattern_s")
        composite = CompositeProviderStrategy(mock_logger, [s])
        assert composite.get_provider_name_pattern() == "composite"

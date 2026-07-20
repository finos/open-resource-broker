"""Extended unit tests for CompositeProviderStrategy — targeting uncovered branches.

Covers:
- composition_config property (line 156)
- execute_operation exception handler (lines 345-348)
- _filter_capable_strategies exception path (lines 365-366)
- _execute_parallel future exception path (lines 387-389)
- _aggregate_merge_all with non-dict, non-list data (line 579)
- _aggregate_merge_all with no results (line 560)
- _aggregate_best_performance with no results (line 586)
- cleanup outer exception path (lines 690-691)
- select_strategy_by_weight edge cases (line 443, 459)
- AGGREGATED mode (line 544)
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.base.strategy.composite_strategy import (
    AggregationPolicy,
    CompositeProviderStrategy,
    CompositionConfig,
    CompositionMode,
    StrategyExecutionResult,
)
from orb.providers.base.strategy.provider_strategy import (
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
# composition_config property (line 156)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeCompositionConfigProperty:
    def test_composition_config_returns_config(self, mock_logger):
        cfg = CompositionConfig(mode=CompositionMode.PARALLEL)
        s = ConcreteProviderStrategy("cfg_prop")
        composite = CompositeProviderStrategy(mock_logger, [s], config=cfg)
        assert composite.composition_config is cfg


# ---------------------------------------------------------------------------
# execute_operation outer exception handler (lines 345-348)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeOuterExceptionHandler:
    def test_outer_exception_returns_composite_error(self, mock_logger):
        s = ConcreteProviderStrategy("exc_outer")
        composite = CompositeProviderStrategy(mock_logger, [s])
        composite.initialize()
        # Patch _filter_capable_strategies to raise so the outer except fires
        with patch.object(
            composite, "_filter_capable_strategies", side_effect=RuntimeError("kaboom")
        ):
            result = _run(composite.execute_operation(make_op()))
        assert not result.success
        assert result.error_code == "COMPOSITE_EXECUTION_ERROR"


# ---------------------------------------------------------------------------
# _filter_capable_strategies exception path (lines 365-366)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeFilterCapableStrategiesException:
    def test_exception_from_get_capabilities_excludes_strategy(self, mock_logger):
        broken = MagicMock()
        broken.provider_type = "broken_caps"
        broken.get_capabilities.side_effect = RuntimeError("caps explode")
        composite = CompositeProviderStrategy(mock_logger, [broken])
        composite.initialize()
        capable = composite._filter_capable_strategies(make_op())
        assert "broken_caps" not in capable
        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# _execute_parallel future exception path (lines 387-389)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeParallelFutureException:
    def test_future_exception_recorded_as_failed_result(self, mock_logger):
        s = ConcreteProviderStrategy(
            "par_fut_exc", operation_result=ProviderResult.success_result({})
        )
        cfg = CompositionConfig(mode=CompositionMode.PARALLEL, failure_threshold=1.0)
        composite = CompositeProviderStrategy(mock_logger, [s], config=cfg)
        composite.initialize()

        # Patch the executor to submit a future that raises
        failing_future: Future = Future()
        failing_future.set_exception(RuntimeError("future explosion"))

        def patched_submit(fn, *args, **kwargs):
            return failing_future

        with patch.object(composite._executor, "submit", side_effect=patched_submit):
            result = _run(composite.execute_operation(make_op()))
        # The future exception is captured as a failed StrategyExecutionResult
        # and propagated through aggregation. With zero successes and
        # min_success_count=1, aggregation returns INSUFFICIENT_SUCCESS.
        # (failure_rate 1.0 is not > failure_threshold 1.0, so the min-success
        # check fires rather than FAILURE_THRESHOLD_EXCEEDED.)
        assert result.success is False
        assert result.error_code == "INSUFFICIENT_SUCCESS"


# ---------------------------------------------------------------------------
# _select_strategy_by_weight edge cases (lines 443, 459)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeSelectStrategyByWeight:
    def test_empty_available_weights_falls_back_to_first(self, mock_logger):
        from orb.providers.base.strategy.provider_strategy import ProviderStrategy

        s: ProviderStrategy = ConcreteProviderStrategy("wt_fall")
        composite = CompositeProviderStrategy(mock_logger, [s])
        composite.initialize()
        # Wipe weights so available_weights is empty
        composite._strategy_weights.clear()
        selected = composite._select_strategy_by_weight({"wt_fall": s})
        assert selected == "wt_fall"

    def test_zero_total_weight_falls_back_to_first(self, mock_logger):
        from orb.providers.base.strategy.provider_strategy import ProviderStrategy

        s: ProviderStrategy = ConcreteProviderStrategy("zero_wt")
        composite = CompositeProviderStrategy(mock_logger, [s])
        composite.initialize()
        composite._strategy_weights["zero_wt"] = 0.0
        selected = composite._select_strategy_by_weight({"zero_wt": s})
        assert selected == "zero_wt"

    def test_weighted_selection_respects_cumulative_weight_buckets(self, mock_logger):
        from orb.providers.base.strategy.provider_strategy import ProviderStrategy

        s_a: ProviderStrategy = ConcreteProviderStrategy("w_a")
        s_b: ProviderStrategy = ConcreteProviderStrategy("w_b")
        composite = CompositeProviderStrategy(mock_logger, [s_a, s_b])
        composite.initialize()
        strategies: dict[str, ProviderStrategy] = {"w_a": s_a, "w_b": s_b}
        # Equal weights (0.5 each); total_weight == 1.0. rand_val = random() *
        # total_weight. Cumulative order is w_a (bucket up to 0.5) then w_b
        # (bucket up to 1.0). Patch the CSPRNG's random() to land in each bucket.
        with patch(
            "orb.providers.base.strategy.composite_strategy.secrets.SystemRandom"
        ) as mock_sysrandom:
            # rand_val = 0.25 <= 0.5 -> first bucket -> w_a
            mock_sysrandom.return_value.random.return_value = 0.25
            assert composite._select_strategy_by_weight(strategies) == "w_a"
            # rand_val = 0.75 > 0.5 -> second bucket -> w_b
            mock_sysrandom.return_value.random.return_value = 0.75
            assert composite._select_strategy_by_weight(strategies) == "w_b"


# ---------------------------------------------------------------------------
# _aggregate_merge_all with non-dict, non-list data (line 579)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeAggregateMergeAllNonContainer:
    def test_non_dict_non_list_data_stored_by_strategy_type(self, mock_logger):
        s = ConcreteProviderStrategy("scalar_agg")
        composite = CompositeProviderStrategy(mock_logger, [s])
        composite.initialize()
        r = StrategyExecutionResult(
            strategy_type="scalar_s",
            result=ProviderResult.success_result("a_string"),  # scalar data
            execution_time_ms=1.0,
            success=True,
        )
        composite._config = CompositionConfig(aggregation_policy=AggregationPolicy.MERGE_ALL)
        out = composite._aggregate_results([r], make_op())
        assert out.success
        assert out.data["scalar_s"] == "a_string"


# ---------------------------------------------------------------------------
# _aggregate_merge_all with empty results (line 560)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeAggregateMergeAllEmpty:
    def test_empty_merge_all_returns_no_results_error(self, mock_logger):
        s = ConcreteProviderStrategy("empty_agg")
        composite = CompositeProviderStrategy(mock_logger, [s])
        composite.initialize()
        out = composite._aggregate_merge_all([])
        assert not out.success
        assert out.error_code == "NO_RESULTS"


# ---------------------------------------------------------------------------
# _aggregate_best_performance with empty results (line 586)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeAggregateBestPerformanceEmpty:
    def test_empty_best_performance_returns_no_results_error(self, mock_logger):
        s = ConcreteProviderStrategy("empty_best")
        composite = CompositeProviderStrategy(mock_logger, [s])
        composite.initialize()
        out = composite._aggregate_best_performance([])
        assert not out.success
        assert out.error_code == "NO_RESULTS"


# ---------------------------------------------------------------------------
# AGGREGATED mode falls through to _execute_parallel (line 544)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeAggregatedMode:
    def test_aggregated_mode_uses_parallel_execution(self, mock_logger):
        s = ConcreteProviderStrategy(
            "agg_mode_s", operation_result=ProviderResult.success_result({"agg": True})
        )
        cfg = CompositionConfig(
            mode=CompositionMode.AGGREGATED,
            aggregation_policy=AggregationPolicy.MERGE_ALL,
        )
        composite = CompositeProviderStrategy(mock_logger, [s], config=cfg)
        composite.initialize()
        result = _run(composite.execute_operation(make_op()))
        assert result.success


# ---------------------------------------------------------------------------
# cleanup outer exception path (lines 690-691)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeCleanupOuterException:
    def test_outer_exception_during_cleanup_is_swallowed(self, mock_logger):
        s = ConcreteProviderStrategy("outer_clean_s")
        composite = CompositeProviderStrategy(mock_logger, [s])
        composite.initialize()
        # Patch executor.shutdown to raise so outer except fires
        with patch.object(composite._executor, "shutdown", side_effect=RuntimeError("shut boom")):
            composite.cleanup()  # Must not raise
        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# REDUNDANT mode (falls through to parallel — same else branch)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompositeRedundantMode:
    def test_redundant_mode_executes_successfully(self, mock_logger):
        s = ConcreteProviderStrategy("red_s", operation_result=ProviderResult.success_result({}))
        cfg = CompositionConfig(mode=CompositionMode.REDUNDANT)
        composite = CompositeProviderStrategy(mock_logger, [s], config=cfg)
        composite.initialize()
        result = _run(composite.execute_operation(make_op()))
        assert result.success

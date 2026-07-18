"""Unit tests for provider_selector module.

Covers: FirstAvailableSelector, RoundRobinSelector, PerformanceBasedSelector,
RandomSelector, SelectorFactory, SelectionCriteria, SelectionResult.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.providers.base.strategy.provider_selector import (
    FirstAvailableSelector,
    PerformanceBasedSelector,
    RandomSelector,
    RoundRobinSelector,
    SelectionCriteria,
    SelectionPolicy,
    SelectionResult,
    SelectorFactory,
)
from orb.providers.base.strategy.provider_strategy import ProviderStrategy
from tests.providers.base.strategy.conftest import ConcreteProviderStrategy, make_op

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_metrics(
    success_rate: float = 100.0,
    avg_response_ms: float = 50.0,
    total_ops: int = 10,
) -> dict:
    return {
        "success_rate": success_rate,
        "average_response_time_ms": avg_response_ms,
        "total_operations": total_ops,
    }


def _strat_dict(*names: str) -> dict[str, ProviderStrategy]:
    """Return a typed dict[str, ProviderStrategy] from names."""
    return {name: ConcreteProviderStrategy(name) for name in names}


@pytest.fixture
def mock_logger():
    return MagicMock()


# ---------------------------------------------------------------------------
# SelectionCriteria defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectionCriteria:
    def test_defaults_are_sane(self):
        c = SelectionCriteria()
        assert c.require_healthy is True
        assert c.min_success_rate == 0.0
        assert c.max_response_time_ms == float("inf")

    def test_post_init_sets_empty_lists(self):
        c = SelectionCriteria()
        assert c.required_capabilities == []
        assert c.exclude_strategies == []
        assert c.prefer_strategies == []

    def test_explicit_values_are_preserved(self):
        c = SelectionCriteria(
            required_capabilities=["cap_a"],
            exclude_strategies=["bad"],
            prefer_strategies=["good"],
        )
        assert c.required_capabilities == ["cap_a"]
        assert c.exclude_strategies == ["bad"]
        assert c.prefer_strategies == ["good"]


# ---------------------------------------------------------------------------
# SelectionResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectionResult:
    def test_success_when_strategy_present(self):
        s: ProviderStrategy = ConcreteProviderStrategy("ok")
        result = SelectionResult(selected_strategy=s, selection_reason="test")
        assert result.success is True

    def test_not_success_when_none(self):
        result = SelectionResult(selected_strategy=None, selection_reason="no match")
        assert result.success is False

    def test_post_init_sets_empty_alternatives(self):
        s: ProviderStrategy = ConcreteProviderStrategy("ok2")
        result = SelectionResult(selected_strategy=s, selection_reason="r")
        assert result.alternatives == []

    def test_explicit_alternatives_preserved(self):
        s: ProviderStrategy = ConcreteProviderStrategy("main")
        alt: ProviderStrategy = ConcreteProviderStrategy("alt")
        result = SelectionResult(
            selected_strategy=s,
            selection_reason="r",
            alternatives=[alt],
        )
        assert result.alternatives is not None
        assert alt in result.alternatives


# ---------------------------------------------------------------------------
# FirstAvailableSelector
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFirstAvailableSelector:
    def test_returns_first_healthy_strategy(self, mock_logger):
        strategies = _strat_dict("fa_a", "fa_b")
        selector = FirstAvailableSelector(mock_logger)
        op = make_op()
        result = selector.select_strategy(strategies, {}, op)
        assert result.success
        assert result.selected_strategy is not None

    def test_returns_none_when_all_unhealthy(self, mock_logger):
        s: ProviderStrategy = ConcreteProviderStrategy("unhealthy_fa", healthy=False)
        selector = FirstAvailableSelector(mock_logger)
        op = make_op()
        result = selector.select_strategy({"unhealthy_fa": s}, {}, op)
        assert not result.success

    def test_excludes_strategy_by_criteria(self, mock_logger):
        strategies = _strat_dict("include_fa", "exclude_fa")
        criteria = SelectionCriteria(exclude_strategies=["include_fa"])
        selector = FirstAvailableSelector(mock_logger)
        op = make_op()
        result = selector.select_strategy(strategies, {}, op, criteria)
        assert result.success
        assert result.selected_strategy is not None
        assert result.selected_strategy.provider_type == "exclude_fa"

    def test_metrics_filter_by_success_rate(self, mock_logger):
        strategies = _strat_dict("slow_fa")
        metrics = {"slow_fa": _make_metrics(success_rate=50.0)}
        criteria = SelectionCriteria(min_success_rate=80.0, require_healthy=False)
        selector = FirstAvailableSelector(mock_logger)
        result = selector.select_strategy(strategies, metrics, make_op(), criteria)
        assert not result.success

    def test_metrics_filter_by_response_time(self, mock_logger):
        strategies = _strat_dict("slow_rt_fa")
        metrics = {"slow_rt_fa": _make_metrics(avg_response_ms=5000.0)}
        criteria = SelectionCriteria(max_response_time_ms=100.0, require_healthy=False)
        selector = FirstAvailableSelector(mock_logger)
        result = selector.select_strategy(strategies, metrics, make_op(), criteria)
        assert not result.success

    def test_custom_filter_excludes_strategy(self, mock_logger):
        s: ProviderStrategy = ConcreteProviderStrategy("cf_fa")
        criteria = SelectionCriteria(
            require_healthy=False,
            custom_filter=lambda strategy, m: False,
        )
        selector = FirstAvailableSelector(mock_logger)
        result = selector.select_strategy({"cf_fa": s}, {}, make_op(), criteria)
        assert not result.success

    def test_custom_filter_accepts_strategy(self, mock_logger):
        s: ProviderStrategy = ConcreteProviderStrategy("cf_ok_fa")
        criteria = SelectionCriteria(
            require_healthy=False,
            custom_filter=lambda strategy, m: True,
        )
        selector = FirstAvailableSelector(mock_logger)
        result = selector.select_strategy({"cf_ok_fa": s}, {}, make_op(), criteria)
        assert result.success

    def test_selection_time_ms_populated(self, mock_logger):
        strategies = _strat_dict("time_fa")
        selector = FirstAvailableSelector(mock_logger)
        result = selector.select_strategy(strategies, {}, make_op())
        assert result.selection_time_ms >= 0

    def test_required_capabilities_missing_feature_excludes(self, mock_logger):
        s: ProviderStrategy = ConcreteProviderStrategy("cap_check_fa")
        criteria = SelectionCriteria(
            require_healthy=False,
            required_capabilities=["nonexistent_feature"],
        )
        selector = FirstAvailableSelector(mock_logger)
        result = selector.select_strategy({"cap_check_fa": s}, {}, make_op(), criteria)
        assert not result.success

    def test_required_capabilities_present_passes(self, mock_logger):
        s: ProviderStrategy = ConcreteProviderStrategy("has_test_cap_fa")
        criteria = SelectionCriteria(
            require_healthy=False,
            required_capabilities=["test"],
        )
        selector = FirstAvailableSelector(mock_logger)
        result = selector.select_strategy({"has_test_cap_fa": s}, {}, make_op(), criteria)
        assert result.success


# ---------------------------------------------------------------------------
# RoundRobinSelector
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRoundRobinSelector:
    def test_returns_strategy_on_first_call(self, mock_logger):
        strategies = _strat_dict("rr_a", "rr_b")
        selector = RoundRobinSelector(mock_logger)
        result = selector.select_strategy(strategies, {}, make_op())
        assert result.success

    def test_round_robin_cycles(self, mock_logger):
        strategies = _strat_dict("rr_x", "rr_y")
        selector = RoundRobinSelector(mock_logger)
        seen = set()
        for _ in range(4):
            r = selector.select_strategy(strategies, {}, make_op())
            if r.selected_strategy:
                seen.add(r.selected_strategy.provider_type)
        assert "rr_x" in seen
        assert "rr_y" in seen

    def test_returns_none_when_no_suitable(self, mock_logger):
        s: ProviderStrategy = ConcreteProviderStrategy("unfit_rr", healthy=False)
        selector = RoundRobinSelector(mock_logger)
        result = selector.select_strategy({"unfit_rr": s}, {}, make_op())
        assert not result.success

    def test_populates_alternatives(self, mock_logger):
        strategies = _strat_dict("alt_a_rr", "alt_b_rr")
        selector = RoundRobinSelector(mock_logger)
        result = selector.select_strategy(strategies, {}, make_op())
        assert result.success
        assert result.alternatives is not None
        assert len(result.alternatives) == 1

    def test_selection_time_positive(self, mock_logger):
        strategies = _strat_dict("timing_rr")
        selector = RoundRobinSelector(mock_logger)
        result = selector.select_strategy(strategies, {}, make_op())
        assert result.selection_time_ms >= 0


# ---------------------------------------------------------------------------
# PerformanceBasedSelector
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPerformanceBasedSelector:
    def test_returns_best_scoring_strategy(self, mock_logger):
        s_good: ProviderStrategy = ConcreteProviderStrategy("perf_good")
        s_bad: ProviderStrategy = ConcreteProviderStrategy("perf_bad")
        metrics = {
            "perf_good": _make_metrics(success_rate=99.0, avg_response_ms=10.0),
            "perf_bad": _make_metrics(success_rate=50.0, avg_response_ms=900.0),
        }
        selector = PerformanceBasedSelector(mock_logger)
        result = selector.select_strategy(
            {"perf_good": s_good, "perf_bad": s_bad},
            metrics,
            make_op(),
            SelectionCriteria(require_healthy=False),
        )
        assert result.success
        assert result.selected_strategy is not None
        assert result.selected_strategy.provider_type == "perf_good"

    def test_returns_none_when_no_candidates(self, mock_logger):
        s: ProviderStrategy = ConcreteProviderStrategy("no_cand_perf", healthy=False)
        selector = PerformanceBasedSelector(mock_logger)
        result = selector.select_strategy({"no_cand_perf": s}, {}, make_op())
        assert not result.success

    def test_zero_total_ops_yields_zero_score(self, mock_logger):
        s: ProviderStrategy = ConcreteProviderStrategy("zero_ops_perf")
        metrics = {"zero_ops_perf": _make_metrics(total_ops=0)}
        selector = PerformanceBasedSelector(mock_logger)
        criteria = SelectionCriteria(require_healthy=False)
        result = selector.select_strategy({"zero_ops_perf": s}, metrics, make_op(), criteria)
        assert result.success

    def test_no_metrics_yields_zero_score(self, mock_logger):
        s: ProviderStrategy = ConcreteProviderStrategy("no_metrics_perf")
        selector = PerformanceBasedSelector(mock_logger)
        criteria = SelectionCriteria(require_healthy=False)
        result = selector.select_strategy({"no_metrics_perf": s}, {}, make_op(), criteria)
        assert result.success

    def test_populates_alternatives(self, mock_logger):
        s_a: ProviderStrategy = ConcreteProviderStrategy("pa_perf")
        s_b: ProviderStrategy = ConcreteProviderStrategy("pb_perf")
        metrics = {
            "pa_perf": _make_metrics(success_rate=100.0, avg_response_ms=5.0),
            "pb_perf": _make_metrics(success_rate=100.0, avg_response_ms=500.0),
        }
        selector = PerformanceBasedSelector(mock_logger)
        criteria = SelectionCriteria(require_healthy=False)
        result = selector.select_strategy(
            {"pa_perf": s_a, "pb_perf": s_b}, metrics, make_op(), criteria
        )
        assert result.alternatives is not None
        assert len(result.alternatives) == 1

    def test_fast_strategy_gets_speed_score_bonus(self, mock_logger):
        metrics = {"fast_perf": _make_metrics(success_rate=100.0, avg_response_ms=1.0)}
        selector = PerformanceBasedSelector(mock_logger)
        score = selector._calculate_performance_score(metrics["fast_perf"])
        assert score > 0.9

    def test_no_avg_response_time_uses_max_speed_score(self, mock_logger):
        selector = PerformanceBasedSelector(mock_logger)
        m = {"success_rate": 100.0, "average_response_time_ms": 0.0, "total_operations": 5}
        score = selector._calculate_performance_score(m)
        assert score == pytest.approx(0.7 * 1.0 + 0.3 * 1.0)


# ---------------------------------------------------------------------------
# RandomSelector
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRandomSelector:
    def test_returns_a_strategy(self, mock_logger):
        strategies = _strat_dict("rand_a", "rand_b")
        selector = RandomSelector(mock_logger)
        result = selector.select_strategy(strategies, {}, make_op())
        assert result.success

    def test_returns_none_when_no_suitable(self, mock_logger):
        s: ProviderStrategy = ConcreteProviderStrategy("bad_rand", healthy=False)
        selector = RandomSelector(mock_logger)
        result = selector.select_strategy({"bad_rand": s}, {}, make_op())
        assert not result.success

    def test_selection_reason_contains_random(self, mock_logger):
        strategies = _strat_dict("rnd_reason")
        selector = RandomSelector(mock_logger)
        result = selector.select_strategy(strategies, {}, make_op())
        assert "Random" in result.selection_reason

    def test_alternatives_populated(self, mock_logger):
        strategies = _strat_dict("rnd_x", "rnd_y")
        selector = RandomSelector(mock_logger)
        result = selector.select_strategy(strategies, {}, make_op())
        assert result.success
        assert result.alternatives is not None
        assert len(result.alternatives) == 1


# ---------------------------------------------------------------------------
# SelectorFactory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectorFactory:
    @pytest.fixture(autouse=True)
    def _restore_selector_registry(self):
        """Snapshot and restore the class-level SelectorFactory._selectors.

        SelectorFactory._selectors is mutable class-level global state. Tests
        that register a custom selector must not leak that registration into
        sibling tests (e.g. the one asserting CUSTOM raises Unsupported),
        regardless of whether their assertions pass or fail.
        """
        snapshot = dict(SelectorFactory._selectors)
        yield
        SelectorFactory._selectors.clear()
        SelectorFactory._selectors.update(snapshot)

    def test_creates_first_available_selector(self, mock_logger):
        selector = SelectorFactory.create_selector(SelectionPolicy.FIRST_AVAILABLE, mock_logger)
        assert isinstance(selector, FirstAvailableSelector)

    def test_creates_round_robin_selector(self, mock_logger):
        selector = SelectorFactory.create_selector(SelectionPolicy.ROUND_ROBIN, mock_logger)
        assert isinstance(selector, RoundRobinSelector)

    def test_creates_performance_selector_for_fastest_response(self, mock_logger):
        selector = SelectorFactory.create_selector(SelectionPolicy.FASTEST_RESPONSE, mock_logger)
        assert isinstance(selector, PerformanceBasedSelector)

    def test_creates_performance_selector_for_highest_success_rate(self, mock_logger):
        selector = SelectorFactory.create_selector(
            SelectionPolicy.HIGHEST_SUCCESS_RATE, mock_logger
        )
        assert isinstance(selector, PerformanceBasedSelector)

    def test_creates_random_selector(self, mock_logger):
        selector = SelectorFactory.create_selector(SelectionPolicy.RANDOM, mock_logger)
        assert isinstance(selector, RandomSelector)

    def test_raises_for_unsupported_policy(self, mock_logger):
        with pytest.raises(ValueError, match="Unsupported"):
            SelectorFactory.create_selector(SelectionPolicy.CUSTOM, mock_logger)

    def test_get_supported_policies_returns_list(self):
        policies = SelectorFactory.get_supported_policies()
        assert len(policies) >= 3
        assert SelectionPolicy.FIRST_AVAILABLE in policies

    def test_register_selector_adds_custom(self, mock_logger):
        class MySelector(RandomSelector):
            pass

        SelectorFactory.register_selector(SelectionPolicy.CUSTOM, MySelector)
        selector = SelectorFactory.create_selector(SelectionPolicy.CUSTOM, mock_logger)
        assert isinstance(selector, MySelector)
        # Registry state is restored by the autouse _restore_selector_registry
        # fixture, so no manual cleanup here (which would only run on success).

    def test_register_selector_rejects_non_subclass(self):
        with pytest.raises(ValueError, match="must inherit"):
            SelectorFactory.register_selector(SelectionPolicy.CUSTOM, object)  # type: ignore[arg-type]

"""Unit tests for FallbackProviderStrategy."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.base.metrics import NoOpProviderMetrics, ProviderMetricsPort
from orb.providers.base.strategy.fallback_strategy import (
    CircuitBreakerState,
    CircuitState,
    FallbackConfig,
    FallbackMode,
    FallbackProviderStrategy,
)
from orb.providers.base.strategy.provider_strategy import (
    ProviderOperationType,
    ProviderResult,
)
from tests.providers.base.strategy.conftest import ConcreteProviderStrategy, make_op

# ---------------------------------------------------------------------------
# FallbackConfig validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackConfig:
    def test_defaults_are_valid(self):
        cfg = FallbackConfig()
        assert cfg.max_retries >= 0
        assert cfg.retry_delay_seconds >= 0
        assert cfg.circuit_breaker_threshold >= 1

    def test_rejects_negative_max_retries(self):
        with pytest.raises(ValueError, match="max_retries"):
            FallbackConfig(max_retries=-1)

    def test_rejects_negative_retry_delay(self):
        with pytest.raises(ValueError, match="retry_delay_seconds"):
            FallbackConfig(retry_delay_seconds=-0.1)

    def test_rejects_zero_circuit_breaker_threshold(self):
        with pytest.raises(ValueError, match="circuit_breaker_threshold"):
            FallbackConfig(circuit_breaker_threshold=0)

    def test_rejects_non_positive_circuit_breaker_timeout(self):
        with pytest.raises(ValueError, match="circuit_breaker_timeout_seconds"):
            FallbackConfig(circuit_breaker_timeout_seconds=0)


# ---------------------------------------------------------------------------
# CircuitBreakerState
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCircuitBreakerState:
    def test_initial_failure_rate_is_zero(self):
        state = CircuitBreakerState()
        assert state.failure_rate == 0.0

    def test_failure_rate_after_one_failure(self):
        state = CircuitBreakerState()
        state.record_failure()
        assert state.failure_rate == pytest.approx(1.0)

    def test_failure_rate_after_success_and_failure(self):
        state = CircuitBreakerState()
        state.record_success()
        state.record_failure()
        assert state.failure_rate == pytest.approx(0.5)

    def test_record_success_resets_failure_count(self):
        state = CircuitBreakerState()
        state.record_failure()
        state.record_failure()
        state.record_success()
        assert state.failure_count == 0

    def test_record_failure_increments_failure_count(self):
        state = CircuitBreakerState()
        state.record_failure()
        state.record_failure()
        assert state.failure_count == 2

    def test_record_success_increments_successful_requests(self):
        state = CircuitBreakerState()
        state.record_success()
        assert state.successful_requests == 1
        assert state.total_requests == 1

    def test_last_success_time_set_on_success(self):
        state = CircuitBreakerState()
        state.record_success()
        assert state.last_success_time is not None

    def test_last_failure_time_set_on_failure(self):
        state = CircuitBreakerState()
        state.record_failure()
        assert state.last_failure_time is not None


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackProviderStrategyConstruction:
    def test_rejects_none_primary_strategy(self, mock_logger):
        with pytest.raises(ValueError, match="Primary strategy"):
            FallbackProviderStrategy(mock_logger, None, [ConcreteProviderStrategy("fb")])  # type: ignore[arg-type]

    def test_rejects_empty_fallback_list(self, mock_logger):
        primary = ConcreteProviderStrategy("p")
        with pytest.raises(ValueError, match="fallback strategy"):
            FallbackProviderStrategy(mock_logger, primary, [])

    def test_provider_type_encodes_primary_and_fallbacks(self, mock_logger):
        primary = ConcreteProviderStrategy("primary_t")
        fallback = ConcreteProviderStrategy("fallback_t")
        strategy = FallbackProviderStrategy(mock_logger, primary, [fallback])
        assert "primary_t" in strategy.provider_type
        assert "fallback_t" in strategy.provider_type

    def test_default_metrics_is_noop(self, mock_logger):
        primary = ConcreteProviderStrategy("p")
        fallback = ConcreteProviderStrategy("f")
        strategy = FallbackProviderStrategy(mock_logger, primary, [fallback])
        assert isinstance(strategy._metrics, NoOpProviderMetrics)

    def test_custom_metrics_is_stored(self, mock_logger):
        primary = ConcreteProviderStrategy("p")
        fallback = ConcreteProviderStrategy("f")
        metrics = MagicMock(spec=ProviderMetricsPort)
        strategy = FallbackProviderStrategy(mock_logger, primary, [fallback], metrics=metrics)
        assert strategy._metrics is metrics


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackStrategyProperties:
    @pytest.fixture
    def strategy(self, mock_logger):
        primary = ConcreteProviderStrategy("primary_prop")
        fallback = ConcreteProviderStrategy("fallback_prop")
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        s.initialize()
        return s

    def test_primary_strategy_property(self, strategy):
        assert strategy.primary_strategy.provider_type == "primary_prop"

    def test_fallback_strategies_returns_copy(self, strategy):
        fb_list = strategy.fallback_strategies
        fb_list.clear()
        assert len(strategy.fallback_strategies) == 1  # original unchanged

    def test_current_strategy_defaults_to_primary(self, strategy):
        assert strategy.current_strategy.provider_type == "primary_prop"

    def test_circuit_state_defaults_to_closed(self, strategy):
        assert strategy.circuit_state == CircuitState.CLOSED

    def test_circuit_metrics_returns_dict(self, strategy):
        metrics = strategy.circuit_metrics
        assert "state" in metrics
        assert "failure_count" in metrics


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackStrategyInitialization:
    def test_initialize_succeeds_when_primary_initializes(self, mock_logger):
        primary = ConcreteProviderStrategy("p_init")
        fallback = ConcreteProviderStrategy("f_init")
        strategy = FallbackProviderStrategy(mock_logger, primary, [fallback])
        assert strategy.initialize() is True
        assert strategy.is_initialized

    def test_initialize_idempotent(self, mock_logger):
        # Spies that always report as uninitialised, so the only thing
        # preventing re-initialisation on the second call is the strategy's
        # own idempotency guard (`if self._initialized: return True`).
        primary = MagicMock()
        primary.provider_type = "p_idem"
        primary.is_initialized = False
        primary.initialize.return_value = True
        fallback = MagicMock()
        fallback.provider_type = "f_idem"
        fallback.is_initialized = False
        fallback.initialize.return_value = True
        strategy = FallbackProviderStrategy(mock_logger, primary, [fallback])
        strategy.initialize()
        strategy.initialize()
        assert strategy.is_initialized
        primary.initialize.assert_called_once()
        fallback.initialize.assert_called_once()

    def test_initialize_counts_already_initialized_primary(self, mock_logger):
        primary = ConcreteProviderStrategy("p_pre")
        primary.initialize()
        fallback = ConcreteProviderStrategy("f_pre")
        strategy = FallbackProviderStrategy(mock_logger, primary, [fallback])
        assert strategy.initialize() is True

    def test_initialize_fails_when_no_strategy_initializes(self, mock_logger):
        broken_primary = MagicMock()
        broken_primary.provider_type = "bad_p"
        broken_primary.is_initialized = False
        broken_primary.initialize.return_value = False

        broken_fallback = MagicMock()
        broken_fallback.provider_type = "bad_f"
        broken_fallback.is_initialized = False
        broken_fallback.initialize.return_value = False

        strategy = FallbackProviderStrategy(mock_logger, broken_primary, [broken_fallback])
        assert strategy.initialize() is False
        assert not strategy.is_initialized

    def test_initialize_tolerates_primary_exception(self, mock_logger):
        bad_primary = MagicMock()
        bad_primary.provider_type = "boom_p"
        bad_primary.is_initialized = False
        bad_primary.initialize.side_effect = RuntimeError("explode")

        good_fallback = ConcreteProviderStrategy("good_f")
        strategy = FallbackProviderStrategy(mock_logger, bad_primary, [good_fallback])
        assert strategy.initialize() is True  # fallback saved it


# ---------------------------------------------------------------------------
# execute_operation — not initialized guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackNotInitialized:
    def test_returns_error_when_not_initialized(self, mock_logger):
        primary = ConcreteProviderStrategy("ui_p")
        fallback = ConcreteProviderStrategy("ui_f")
        strategy = FallbackProviderStrategy(mock_logger, primary, [fallback])
        result = asyncio.run(strategy.execute_operation(make_op()))
        assert not result.success
        assert result.error_code == "NOT_INITIALIZED"


# ---------------------------------------------------------------------------
# IMMEDIATE mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackImmediateMode:
    @pytest.fixture
    def strategy_with_healthy_primary(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "im_primary",
            operation_result=ProviderResult.success_result({"from": "primary"}),
        )
        fallback = ConcreteProviderStrategy(
            "im_fallback",
            operation_result=ProviderResult.success_result({"from": "fallback"}),
        )
        cfg = FallbackConfig(mode=FallbackMode.IMMEDIATE)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        return s

    def test_uses_primary_when_healthy(self, strategy_with_healthy_primary):
        result = asyncio.run(strategy_with_healthy_primary.execute_operation(make_op()))
        assert result.success
        assert strategy_with_healthy_primary.current_strategy.provider_type == "im_primary"

    def test_falls_back_when_primary_returns_error(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "fail_p",
            operation_result=ProviderResult.error_result("primary failed", "ERR"),
        )
        fallback = ConcreteProviderStrategy(
            "ok_f",
            operation_result=ProviderResult.success_result({"from": "fallback"}),
        )
        cfg = FallbackConfig(mode=FallbackMode.IMMEDIATE)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        result = asyncio.run(s.execute_operation(make_op()))
        assert result.success
        assert s.current_strategy.provider_type == "ok_f"

    def test_falls_back_when_primary_raises_exception(self, mock_logger):
        primary = ConcreteProviderStrategy("exc_p", operation_raises=RuntimeError("bang"))
        fallback = ConcreteProviderStrategy(
            "exc_f",
            operation_result=ProviderResult.success_result({"recovered": True}),
        )
        cfg = FallbackConfig(mode=FallbackMode.IMMEDIATE)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        result = asyncio.run(s.execute_operation(make_op()))
        assert result.success

    def test_all_fail_without_graceful_degradation_returns_error(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "all_fail_p",
            operation_result=ProviderResult.error_result("fail", "ERR"),
        )
        fallback = ConcreteProviderStrategy(
            "all_fail_f",
            operation_result=ProviderResult.error_result("also fail", "ERR"),
        )
        cfg = FallbackConfig(mode=FallbackMode.IMMEDIATE, enable_graceful_degradation=False)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        result = asyncio.run(s.execute_operation(make_op()))
        assert not result.success
        assert result.error_code == "ALL_STRATEGIES_FAILED"

    def test_routing_info_populated_on_success(self, strategy_with_healthy_primary):
        result = asyncio.run(strategy_with_healthy_primary.execute_operation(make_op()))
        assert result.routing_info is not None
        assert "fallback_mode" in result.routing_info
        assert "circuit_state" in result.routing_info

    def test_fallback_metrics_recorded(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "m_primary",
            operation_result=ProviderResult.error_result("fail", "ERR"),
        )
        fallback = ConcreteProviderStrategy(
            "m_fallback",
            operation_result=ProviderResult.success_result({}),
        )
        metrics = MagicMock(spec=ProviderMetricsPort)
        cfg = FallbackConfig(mode=FallbackMode.IMMEDIATE)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg, metrics=metrics)
        s.initialize()
        asyncio.run(s.execute_operation(make_op()))
        metrics.record_counter.assert_called_once()
        call_kwargs = metrics.record_counter.call_args
        assert call_kwargs[0][0] == "provider.fallback.total"


# ---------------------------------------------------------------------------
# CIRCUIT_BREAKER mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackCircuitBreakerMode:
    def _make_cb_strategy(self, mock_logger, primary, fallback, threshold=2):
        cfg = FallbackConfig(
            mode=FallbackMode.CIRCUIT_BREAKER,
            circuit_breaker_threshold=threshold,
            circuit_breaker_timeout_seconds=60.0,
        )
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        return s

    def test_circuit_stays_closed_on_success(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "cb_p", operation_result=ProviderResult.success_result({})
        )
        fallback = ConcreteProviderStrategy("cb_f")
        s = self._make_cb_strategy(mock_logger, primary, fallback)
        asyncio.run(s.execute_operation(make_op()))
        assert s.circuit_state == CircuitState.CLOSED

    def test_circuit_opens_after_threshold_failures(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "cb_fail_p",
            operation_result=ProviderResult.error_result("fail", "ERR"),
        )
        fallback = ConcreteProviderStrategy(
            "cb_fail_f", operation_result=ProviderResult.success_result({})
        )
        s = self._make_cb_strategy(mock_logger, primary, fallback, threshold=2)
        for _ in range(2):
            asyncio.run(s.execute_operation(make_op()))
        assert s.circuit_state == CircuitState.OPEN

    def test_open_circuit_skips_primary_and_uses_fallback(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "open_p",
            operation_result=ProviderResult.error_result("fail", "ERR"),
        )
        fallback = ConcreteProviderStrategy(
            "open_f", operation_result=ProviderResult.success_result({"used": "fallback"})
        )
        # Spy on primary.execute_operation without altering its behaviour.
        primary_spy = AsyncMock(wraps=primary.execute_operation)
        primary.execute_operation = primary_spy  # type: ignore[method-assign]
        s = self._make_cb_strategy(mock_logger, primary, fallback, threshold=1)
        # Open the circuit — this first call DOES hit the primary once.
        asyncio.run(s.execute_operation(make_op()))
        assert s.circuit_state == CircuitState.OPEN
        assert primary_spy.await_count == 1
        # Next call: circuit is OPEN and timeout (60s) has not elapsed, so the
        # primary must be short-circuited and only the fallback used.
        result = asyncio.run(s.execute_operation(make_op()))
        assert result.success
        assert result.data == {"used": "fallback"}
        # Primary was NOT invoked again on the post-open call.
        assert primary_spy.await_count == 1

    def test_circuit_recovers_to_closed_after_half_open_success(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "recover_p",
            operation_result=ProviderResult.success_result({"recovered": True}),
        )
        fallback = ConcreteProviderStrategy("recover_f")
        metrics = MagicMock(spec=ProviderMetricsPort)
        cfg = FallbackConfig(
            mode=FallbackMode.CIRCUIT_BREAKER,
            circuit_breaker_threshold=1,
            circuit_breaker_timeout_seconds=0.001,  # expire almost immediately
        )
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg, metrics=metrics)
        s.initialize()
        # Force circuit open with a real (truthy) timestamp far enough in the past
        s._circuit_state.state = CircuitState.OPEN
        s._circuit_state.last_failure_time = 1.0  # truthy, old enough → timeout elapsed
        # Execute should transition to half-open and succeed → closed
        result = asyncio.run(s.execute_operation(make_op()))
        assert result.success
        assert s.circuit_state == CircuitState.CLOSED
        metrics.record_counter.assert_called_with(
            "circuit_breaker.closed.total",
            labels={"provider": "recover_p"},
        )

    def test_circuit_opens_on_exception_from_primary(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "exc_cb_p", operation_raises=RuntimeError("network failure")
        )
        fallback = ConcreteProviderStrategy(
            "exc_cb_f", operation_result=ProviderResult.success_result({})
        )
        metrics = MagicMock(spec=ProviderMetricsPort)
        cfg = FallbackConfig(
            mode=FallbackMode.CIRCUIT_BREAKER,
            circuit_breaker_threshold=1,
        )
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg, metrics=metrics)
        s.initialize()
        asyncio.run(s.execute_operation(make_op()))
        assert s.circuit_state == CircuitState.OPEN
        metrics.record_counter.assert_any_call(
            "circuit_breaker.opened.total",
            labels={"provider": "exc_cb_p"},
        )


# ---------------------------------------------------------------------------
# RETRY_THEN_FALLBACK mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackRetryMode:
    def test_succeeds_on_first_attempt_no_retries_needed(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "retry_p", operation_result=ProviderResult.success_result({"try": 1})
        )
        fallback = ConcreteProviderStrategy("retry_f")
        cfg = FallbackConfig(
            mode=FallbackMode.RETRY_THEN_FALLBACK, max_retries=2, retry_delay_seconds=0
        )
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        result = asyncio.run(s.execute_operation(make_op()))
        assert result.success
        assert s.current_strategy.provider_type == "retry_p"

    def test_uses_fallback_after_max_retries_exhausted(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "exhaust_p",
            operation_result=ProviderResult.error_result("always fails", "ERR"),
        )
        fallback = ConcreteProviderStrategy(
            "exhaust_f", operation_result=ProviderResult.success_result({"rescued": True})
        )
        cfg = FallbackConfig(
            mode=FallbackMode.RETRY_THEN_FALLBACK, max_retries=0, retry_delay_seconds=0
        )
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        result = asyncio.run(s.execute_operation(make_op()))
        assert result.success
        assert s.current_strategy.provider_type == "exhaust_f"


# ---------------------------------------------------------------------------
# HEALTH_BASED mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackHealthBasedMode:
    def test_uses_primary_when_marked_healthy(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "hb_p", operation_result=ProviderResult.success_result({"src": "primary"})
        )
        fallback = ConcreteProviderStrategy("hb_f")
        cfg = FallbackConfig(mode=FallbackMode.HEALTH_BASED)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        result = asyncio.run(s.execute_operation(make_op()))
        assert result.success
        assert s.current_strategy.provider_type == "hb_p"

    def test_falls_to_fallback_when_primary_marked_unhealthy(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "hb_bad_p",
            operation_result=ProviderResult.error_result("bad", "ERR"),
        )
        fallback = ConcreteProviderStrategy(
            "hb_good_f", operation_result=ProviderResult.success_result({})
        )
        cfg = FallbackConfig(mode=FallbackMode.HEALTH_BASED)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        s._primary_healthy = False  # mark unhealthy directly
        result = asyncio.run(s.execute_operation(make_op()))
        assert result.success
        assert s.current_strategy.provider_type == "hb_good_f"

    def test_marks_primary_unhealthy_on_failure(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "mark_p",
            operation_result=ProviderResult.error_result("fail", "ERR"),
        )
        fallback = ConcreteProviderStrategy(
            "mark_f", operation_result=ProviderResult.success_result({})
        )
        cfg = FallbackConfig(mode=FallbackMode.HEALTH_BASED)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        asyncio.run(s.execute_operation(make_op()))
        assert not s._primary_healthy


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackGracefulDegradation:
    @pytest.fixture
    def all_fail_strategy(self, mock_logger):
        primary = ConcreteProviderStrategy(
            "gd_p", operation_result=ProviderResult.error_result("fail", "ERR")
        )
        fallback = ConcreteProviderStrategy(
            "gd_f", operation_result=ProviderResult.error_result("also fail", "ERR")
        )
        cfg = FallbackConfig(mode=FallbackMode.IMMEDIATE, enable_graceful_degradation=True)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback], config=cfg)
        s.initialize()
        return s

    def test_health_check_degraded_returns_success_with_false_healthy(self, all_fail_strategy):
        op = make_op(ProviderOperationType.HEALTH_CHECK)
        result = asyncio.run(all_fail_strategy.execute_operation(op))
        assert result.success
        assert result.data["is_healthy"] is False
        assert result.data["status"] == "degraded"

    def test_get_templates_degraded_returns_empty_list(self, all_fail_strategy):
        op = make_op(ProviderOperationType.GET_AVAILABLE_TEMPLATES)
        result = asyncio.run(all_fail_strategy.execute_operation(op))
        assert result.success
        assert result.data["templates"] == []

    def test_other_operation_degraded_returns_error(self, all_fail_strategy):
        op = make_op(ProviderOperationType.CREATE_INSTANCES)
        result = asyncio.run(all_fail_strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "DEGRADED_MODE"


# ---------------------------------------------------------------------------
# get_capabilities / check_health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackCapabilitiesAndHealth:
    @pytest.fixture
    def strategy(self, mock_logger):
        primary = ConcreteProviderStrategy("cap_primary", healthy=True)
        fallback = ConcreteProviderStrategy("cap_fallback", healthy=True)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        s.initialize()
        return s

    def test_get_capabilities_merges_primary_and_fallback(self, strategy):
        caps = strategy.get_capabilities()
        assert len(caps.supported_operations) > 0
        assert caps.features["fallback_enabled"] is True

    def test_get_capabilities_includes_circuit_breaker_flag(self, strategy):
        caps = strategy.get_capabilities()
        assert "circuit_breaker" in caps.features

    def test_get_capabilities_tolerates_primary_exception(self, mock_logger):
        broken_p = MagicMock()
        broken_p.provider_type = "broken_cap_p"
        broken_p.get_capabilities.side_effect = RuntimeError("no caps")
        fallback = ConcreteProviderStrategy("cap_fb")
        s = FallbackProviderStrategy(mock_logger, broken_p, [fallback])
        s.initialize()
        caps = s.get_capabilities()
        assert caps is not None
        mock_logger.warning.assert_called()

    def test_check_health_healthy_when_primary_healthy(self, strategy):
        status = strategy.check_health()
        assert status.is_healthy

    def test_check_health_healthy_when_primary_down_but_fallback_up(self, mock_logger):
        primary = ConcreteProviderStrategy("down_p", healthy=False)
        fallback = ConcreteProviderStrategy("up_f", healthy=True)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        s.initialize()
        status = s.check_health()
        assert status.is_healthy

    def test_check_health_unhealthy_when_all_down(self, mock_logger):
        primary = ConcreteProviderStrategy("all_down_p", healthy=False)
        fallback = ConcreteProviderStrategy("all_down_f", healthy=False)
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        s.initialize()
        status = s.check_health()
        assert not status.is_healthy

    def test_check_health_tolerates_primary_exception(self, mock_logger):
        broken_p = MagicMock()
        broken_p.provider_type = "broken_health_p"
        broken_p.check_health.side_effect = RuntimeError("health check failed")
        fallback = ConcreteProviderStrategy("healthy_fb", healthy=True)
        s = FallbackProviderStrategy(mock_logger, broken_p, [fallback])
        s.initialize()
        status = s.check_health()
        assert status.is_healthy  # fallback is up


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackStrategyCleanup:
    def test_cleanup_marks_not_initialized(self, mock_logger):
        primary = ConcreteProviderStrategy("cleanup_p")
        fallback = ConcreteProviderStrategy("cleanup_f")
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        s.initialize()
        s.cleanup()
        assert not s.is_initialized

    def test_cleanup_calls_primary_cleanup(self, mock_logger):
        primary = ConcreteProviderStrategy("cln_p")
        primary.initialize()
        fallback = ConcreteProviderStrategy("cln_f")
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        s.initialize()
        s.cleanup()
        assert not primary._initialized

    def test_cleanup_tolerates_fallback_exception(self, mock_logger):
        primary = ConcreteProviderStrategy("cln_p2")
        broken_fallback = MagicMock()
        broken_fallback.provider_type = "cln_bad_f"
        broken_fallback.cleanup.side_effect = RuntimeError("can't clean")
        s = FallbackProviderStrategy(mock_logger, primary, [broken_fallback])
        s.initialize()
        s.cleanup()  # must not raise
        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# String representations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackStrategyRepresentations:
    def test_str_contains_primary_type(self, mock_logger):
        primary = ConcreteProviderStrategy("str_p")
        fallback = ConcreteProviderStrategy("str_f")
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        assert "str_p" in str(s)

    def test_repr_contains_circuit_state(self, mock_logger):
        primary = ConcreteProviderStrategy("repr_p")
        fallback = ConcreteProviderStrategy("repr_f")
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        assert "closed" in repr(s)

    def test_generate_provider_name_returns_provider_type(self, mock_logger):
        primary = ConcreteProviderStrategy("gn_p")
        fallback = ConcreteProviderStrategy("gn_f")
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        assert s.generate_provider_name({}) == s.provider_type

    def test_parse_provider_name_returns_dict(self, mock_logger):
        primary = ConcreteProviderStrategy("parse_p")
        fallback = ConcreteProviderStrategy("parse_f")
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        assert s.parse_provider_name("x")["provider_type"] == "x"

    def test_get_provider_name_pattern_returns_fallback(self, mock_logger):
        primary = ConcreteProviderStrategy("pnp_p")
        fallback = ConcreteProviderStrategy("pnp_f")
        s = FallbackProviderStrategy(mock_logger, primary, [fallback])
        assert s.get_provider_name_pattern() == "fallback"

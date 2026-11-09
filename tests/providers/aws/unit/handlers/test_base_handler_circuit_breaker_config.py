"""Tests for circuit breaker config wiring in AWSHandler._get_circuit_breaker_config.

Also exercises the *runtime* retry / circuit-breaker behaviour of
``_retry_with_backoff`` (transient-error retry, non-retryable pass-through,
and circuit opening after the failure threshold) — not just the config wiring.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from orb.providers.aws.exceptions.aws_exceptions import NetworkError
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler

# ---------------------------------------------------------------------------
# Minimal concrete subclass — only implements the abstract methods
# ---------------------------------------------------------------------------


class _ConcreteHandler(AWSHandler):
    def _acquire_hosts_internal(self, request, aws_template):  # type: ignore[override]
        pass  # type: ignore[return]

    def check_hosts_status(self, request):  # type: ignore[override]
        return []

    def release_hosts(self, machine_ids, resource_mapping=None, request_id=""):  # type: ignore[override]
        pass

    def cancel_resource(self, resource_id, request_id):  # type: ignore[override]
        return {}

    @classmethod
    def get_example_templates(cls):
        return []


def _make_handler(config_port=None) -> _ConcreteHandler:
    """Build a _ConcreteHandler with all required deps mocked."""
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    aws_ops.set_retry_method = MagicMock()
    aws_ops.set_pagination_method = MagicMock()
    launch_template_manager = MagicMock()

    return _ConcreteHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=launch_template_manager,
        config_port=config_port,
    )


def _make_config_port(failure_threshold: int, recovery_timeout: int, half_open_timeout: int = 30):
    """Build a mock ConfigurationPort with circuit_breaker fields set."""
    cb = MagicMock()
    cb.failure_threshold = failure_threshold
    cb.recovery_timeout = recovery_timeout
    cb.half_open_timeout = half_open_timeout

    app_config = MagicMock()
    app_config.circuit_breaker = cb

    port = MagicMock()
    port.app_config = app_config
    return port


# ---------------------------------------------------------------------------
# Test 1: returns defaults when config_port is None
# ---------------------------------------------------------------------------


class TestGetCircuitBreakerConfigDefaults:
    def test_returns_defaults_when_no_config_port(self):
        handler = _make_handler(config_port=None)
        result = handler._get_circuit_breaker_config()

        assert result["failure_threshold"] == 5
        assert result["reset_timeout"] == 60
        assert result["half_open_timeout"] == 30


# ---------------------------------------------------------------------------
# Test 2: reads values from config_port.app_config.circuit_breaker
# ---------------------------------------------------------------------------


class TestGetCircuitBreakerConfigFromPort:
    def test_reads_failure_threshold_and_recovery_timeout(self):
        port = _make_config_port(failure_threshold=10, recovery_timeout=120)
        handler = _make_handler(config_port=port)
        result = handler._get_circuit_breaker_config()

        assert result["failure_threshold"] == 10
        assert result["reset_timeout"] == 120  # recovery_timeout → reset_timeout mapping
        assert result["half_open_timeout"] == 30

    def test_half_open_timeout_is_read_from_config(self):
        port = _make_config_port(failure_threshold=5, recovery_timeout=60, half_open_timeout=7)
        handler = _make_handler(config_port=port)

        assert handler._get_circuit_breaker_config()["half_open_timeout"] == 7

    def test_custom_values_are_reflected(self):
        port = _make_config_port(failure_threshold=3, recovery_timeout=45)
        handler = _make_handler(config_port=port)
        result = handler._get_circuit_breaker_config()

        assert result["failure_threshold"] == 3
        assert result["reset_timeout"] == 45


# ---------------------------------------------------------------------------
# Test 3: falls back gracefully when app_config raises AttributeError
# ---------------------------------------------------------------------------


class TestGetCircuitBreakerConfigFallback:
    def test_falls_back_on_attribute_error(self):
        # Use a plain object whose app_config property raises AttributeError
        class _BadPort:
            @property
            def app_config(self):
                raise AttributeError("no app_config")

        handler = _make_handler(config_port=_BadPort())
        result = handler._get_circuit_breaker_config()

        assert result["failure_threshold"] == 5
        assert result["reset_timeout"] == 60
        assert result["half_open_timeout"] == 30

    def test_falls_back_when_circuit_breaker_attr_missing(self):
        # app_config exists but has no circuit_breaker attribute
        class _AppConfigNoCB:
            pass

        class _PortNoCB:
            app_config = _AppConfigNoCB()

        handler = _make_handler(config_port=_PortNoCB())
        result = handler._get_circuit_breaker_config()

        assert result["failure_threshold"] == 5
        assert result["reset_timeout"] == 60


# ---------------------------------------------------------------------------
# Test 4: _get_retry_strategy_config("critical") uses config values
# ---------------------------------------------------------------------------


class TestRetryStrategyConfigCritical:
    def test_critical_uses_config_port_values(self):
        port = _make_config_port(failure_threshold=8, recovery_timeout=90)
        handler = _make_handler(config_port=port)
        config = handler._get_retry_strategy_config("critical", "ec2", "run_instances")

        assert config["strategy"] == "circuit_breaker"
        assert config["failure_threshold"] == 8
        assert config["reset_timeout"] == 90
        assert config["half_open_timeout"] == 30
        assert config["count_per_call"] is True
        assert config["service"].endswith(":run_instances")

    def test_critical_uses_defaults_when_no_config_port(self):
        handler = _make_handler(config_port=None)
        config = handler._get_retry_strategy_config("critical", "ec2", "run_instances")

        assert config["failure_threshold"] == 5
        assert config["reset_timeout"] == 60

    def test_critical_requires_an_operation_name(self):
        handler = _make_handler(config_port=None)

        with pytest.raises(ValueError):
            handler._get_retry_strategy_config("critical", "ec2")


# ---------------------------------------------------------------------------
# Test 5: non-critical paths are unaffected
# ---------------------------------------------------------------------------


class TestRetryStrategyConfigNonCritical:
    def test_standard_strategy_unchanged(self):
        port = _make_config_port(failure_threshold=99, recovery_timeout=999)
        handler = _make_handler(config_port=port)
        config = handler._get_retry_strategy_config("standard", "ec2")

        assert config["strategy"] == "exponential"
        assert "failure_threshold" not in config
        assert "reset_timeout" not in config

    def test_read_only_strategy_unchanged(self):
        port = _make_config_port(failure_threshold=99, recovery_timeout=999)
        handler = _make_handler(config_port=port)
        config = handler._get_retry_strategy_config("read_only", "ec2")

        assert config["strategy"] == "exponential"
        assert "failure_threshold" not in config


# ---------------------------------------------------------------------------
# Test 6: runtime retry / circuit-breaker behaviour (not just config wiring)
# ---------------------------------------------------------------------------


def _throttle_error() -> ClientError:
    return ClientError(
        {"Error": {"Code": "RequestLimitExceeded", "Message": "slow down"}}, "DescribeInstances"
    )


def _service_unavailable() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ServiceUnavailable", "Message": "boom"}}, "run_instances"
    )


@pytest.fixture(autouse=True)
def _reset_circuit_states():
    """Circuit state is class-level shared — clear it around each runtime test."""
    from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy

    CircuitBreakerStrategy._circuit_states.clear()
    yield
    CircuitBreakerStrategy._circuit_states.clear()


class TestRetryRuntimeBehaviour:
    def test_transient_error_is_retried_then_succeeds(self):
        """A transient throttle is retried and the eventual success is returned."""
        handler = _make_handler(config_port=None)
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise _throttle_error()
            return "ok"

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep") as sleep:
            result = handler._retry_with_backoff(flaky, operation_type="read_only")

        assert result == "ok"
        assert calls["n"] == 2  # first attempt failed, retry succeeded
        assert sleep.call_count == 1  # slept once before the retry

    def test_success_first_call_does_not_sleep(self):
        """A call that succeeds immediately is not retried and never sleeps."""
        handler = _make_handler(config_port=None)
        func = MagicMock(return_value="done")

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep") as sleep:
            result = handler._retry_with_backoff(func, operation_type="read_only")

        assert result == "done"
        assert func.call_count == 1
        assert sleep.call_count == 0

    def test_circuit_opens_after_failure_threshold(self):
        """Critical operations trip the breaker after the threshold of exhausted calls."""
        from orb.infrastructure.resilience.exceptions import CircuitBreakerOpenError
        from orb.infrastructure.resilience.strategy.circuit_breaker import (
            CircuitBreakerStrategy,
            CircuitState,
        )

        port = _make_config_port(failure_threshold=3, recovery_timeout=60)
        handler = _make_handler(config_port=port)
        func = MagicMock(side_effect=_service_unavailable())
        func.__name__ = "run_instances"

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"):
            for _ in range(3):
                with pytest.raises(NetworkError):
                    handler._retry_with_backoff(func, operation_type="critical")
            calls_before = func.call_count
            with pytest.raises(CircuitBreakerOpenError):
                handler._retry_with_backoff(func, operation_type="critical")

        # The fourth call is rejected before the function is invoked.
        assert func.call_count == calls_before
        key = handler._call_executor.circuit_breaker_key("run_instances")
        circuit = CircuitBreakerStrategy._circuit_states[key]
        assert circuit["state"] == CircuitState.OPEN
        assert circuit["failure_count"] == 3

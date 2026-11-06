"""AWSCallExecutor: classified retries, per-operation breakers, typed errors."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError

from orb.infrastructure.resilience import retry_classifier_registry as registry
from orb.infrastructure.resilience.exceptions import (
    CircuitBreakerOpenError,
    MaxRetriesExceededError,
)
from orb.infrastructure.resilience.strategy.circuit_breaker import (
    CircuitBreakerStrategy,
    CircuitState,
)
from orb.providers.aws.exceptions.aws_exceptions import (
    AWSInfrastructureError,
    ResourceInUseError,
)
from orb.providers.aws.resilience.call_executor import (
    EVENTUAL_CONSISTENCY_ERRORS,
    AWSCallExecutor,
)

SLEEP = "orb.infrastructure.resilience.retry_decorator.time.sleep"


def _error(code: str, status: int = 400) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "m"}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "Op",
    )


def _converter(error: ClientError, operation: str) -> Exception:
    code = error.response["Error"]["Code"]
    if code == "ResourceInUse":
        return ResourceInUseError("in use", aws_error_code=code)
    return AWSInfrastructureError(f"AWS Error: {code}", aws_error_code=code)


@pytest.fixture
def executor():
    from orb.providers.aws.resilience.retry_classifier import register_aws_retry_classifier

    saved = list(registry._classifiers)
    registry._classifiers.clear()
    register_aws_retry_classifier()
    yield AWSCallExecutor(
        MagicMock(),
        handler_type=f"h{uuid.uuid4().hex[:6]}",
        provider_name="p",
        attempts_provider=lambda: (3, 2),
        error_converter=_converter,
    )
    registry._classifiers[:] = saved


def _fn(side_effect, name="describe_things"):
    func = MagicMock(side_effect=side_effect)
    func.__name__ = name
    return func


@pytest.mark.unit
class TestRetryClassification:
    @pytest.mark.parametrize("code", ["InvalidParameterValue", "UnauthorizedOperation"])
    def test_permanent_error_is_attempted_once_without_sleeping(self, executor, code):
        func = _fn(_error(code))
        with patch(SLEEP) as sleep:
            with pytest.raises(AWSInfrastructureError):
                executor.execute(func)
        assert func.call_count == 1
        assert sleep.call_count == 0

    def test_throttle_twice_then_success(self, executor):
        func = _fn([_error("Throttling"), _error("RequestLimitExceeded"), "ok"])
        with patch(SLEEP) as sleep:
            assert executor.execute(func) == "ok"
        assert func.call_count == 3
        assert sleep.call_count == 2

    def test_connection_errors_are_retried(self, executor):
        func = _fn([ConnectTimeoutError(endpoint_url="https://ec2"), "ok"])
        with patch(SLEEP):
            assert executor.execute(func) == "ok"
        assert func.call_count == 2

    def test_non_retryable_errors_argument_is_honoured(self, executor):
        func = _fn(_error("Throttling"))
        with patch(SLEEP) as sleep:
            with pytest.raises(AWSInfrastructureError):
                executor.execute(func, non_retryable_errors=["Throttling"])
        assert func.call_count == 1
        assert sleep.call_count == 0

    def test_attempt_budget_follows_the_configured_retries(self, executor):
        func = _fn(_error("Throttling"))
        with patch(SLEEP):
            with pytest.raises(AWSInfrastructureError):
                executor.execute(func)
        assert func.call_count == 4  # 3 retries + the first call

        func = _fn(_error("Throttling"))
        with patch(SLEEP):
            with pytest.raises(AWSInfrastructureError):
                executor.execute(func, operation_type="read_only")
        assert func.call_count == 3  # 2 retries + the first call

    def test_eventual_consistency_retry_applies_only_to_calls_that_ask(self, executor):
        not_found = _error("InvalidInstanceID.NotFound")

        func = _fn([not_found, not_found, "ok"], name="describe_instances")
        with patch(SLEEP):
            result = executor.execute(
                func, operation_type="read_only", retryable_errors=EVENTUAL_CONSISTENCY_ERRORS
            )
        assert result == "ok"
        assert func.call_count == 3

        terminate = _fn(not_found, name="terminate_instances")
        with patch(SLEEP) as sleep:
            with pytest.raises(AWSInfrastructureError):
                executor.execute(terminate)
        assert terminate.call_count == 1
        assert sleep.call_count == 0


@pytest.mark.unit
class TestExhaustionContract:
    @pytest.mark.parametrize(
        "code, expected",
        [
            ("ResourceInUse", ResourceInUseError),
            ("ScalingActivityInProgress", AWSInfrastructureError),
        ],
    )
    def test_exhaustion_raises_the_typed_error_with_code_and_cause(self, executor, code, expected):
        original = _error(code)
        func = _fn(original, name="delete_auto_scaling_group")
        with patch(SLEEP):
            with pytest.raises(expected) as caught:
                executor.execute(func, operation_type="critical")
        assert not isinstance(caught.value, MaxRetriesExceededError)
        assert caught.value.aws_error_code == code  # type: ignore[attr-defined]
        assert caught.value.__cause__ is original
        assert func.call_count == 4

    def test_preserve_client_error_returns_the_raw_error(self, executor):
        original = _error("Throttling")
        func = _fn(original)
        with patch(SLEEP):
            with pytest.raises(ClientError) as caught:
                executor.execute(func, preserve_client_error=True)
        assert caught.value is original

    def test_exhausted_non_aws_error_is_the_original_exception(self, executor):
        func = _fn(RuntimeError("boom"))
        with patch(SLEEP):
            with pytest.raises(RuntimeError, match="boom"):
                executor.execute(func)


@pytest.mark.unit
class TestCircuitBreakerKey:
    def test_critical_call_without_a_usable_name_is_rejected(self, executor):
        with pytest.raises(ValueError, match="operation_name"):
            executor.execute(lambda: None, operation_type="critical")
        with pytest.raises(ValueError, match="operation_name"):
            executor.execute(MagicMock(), operation_type="critical")

    def test_explicit_name_keys_the_breaker(self, executor):
        config = executor.strategy_config("critical", "run_instances")
        assert config["service"] == f"aws:p:{executor._handler_type}:run_instances"
        assert config["count_per_call"] is True

    def test_one_failing_operation_leaves_others_closed(self, executor):
        threshold = 2
        executor._config_port = MagicMock()
        executor._config_port.app_config.circuit_breaker.failure_threshold = threshold
        executor._config_port.app_config.circuit_breaker.recovery_timeout = 60
        executor._config_port.app_config.circuit_breaker.half_open_timeout = 30

        failing = _fn(_error("ServiceUnavailable", 503))
        with patch(SLEEP):
            for _ in range(threshold):
                with pytest.raises(Exception):
                    executor.execute(failing, operation_type="critical", operation_name="op_a")

        key_a = executor.circuit_breaker_key("op_a")
        key_b = executor.circuit_breaker_key("op_b")
        assert CircuitBreakerStrategy._circuit_states[key_a]["state"] == CircuitState.OPEN

        healthy = _fn(["fine"], name="op_b")
        assert executor.execute(healthy, operation_type="critical", operation_name="op_b") == "fine"
        assert CircuitBreakerStrategy._circuit_states[key_b]["state"] == CircuitState.CLOSED

        again = _fn(["never"])
        with pytest.raises(CircuitBreakerOpenError):
            executor.execute(again, operation_type="critical", operation_name="op_a")
        assert again.call_count == 0

    def test_permanent_errors_do_not_open_the_breaker(self, executor):
        executor._config_port = MagicMock()
        executor._config_port.app_config.circuit_breaker.failure_threshold = 2
        executor._config_port.app_config.circuit_breaker.recovery_timeout = 60
        executor._config_port.app_config.circuit_breaker.half_open_timeout = 30

        failing = _fn(_error("InvalidParameterValue"))
        for _ in range(5):
            with pytest.raises(AWSInfrastructureError):
                executor.execute(failing, operation_type="critical", operation_name="op_perm")
        state = CircuitBreakerStrategy._circuit_states[executor.circuit_breaker_key("op_perm")]
        assert state["failure_count"] == 0
        assert state["state"] == CircuitState.CLOSED

    def test_circuit_open_error_is_chained_from_the_last_real_error(self, executor):
        executor._config_port = MagicMock()
        executor._config_port.app_config.circuit_breaker.failure_threshold = 1
        executor._config_port.app_config.circuit_breaker.recovery_timeout = 60
        executor._config_port.app_config.circuit_breaker.half_open_timeout = 30
        opener = CircuitBreakerStrategy(service_name=executor.circuit_breaker_key("op_c"))

        def trip_and_fail():
            opener._force_open(executor.circuit_breaker_key("op_c"))
            raise _error("Throttling")

        func = _fn(trip_and_fail)
        with patch(SLEEP):
            with pytest.raises(CircuitBreakerOpenError) as caught:
                executor.execute(func, operation_type="critical", operation_name="op_c")
        assert isinstance(caught.value.__cause__, ClientError)
        assert func.call_count == 1


@pytest.mark.unit
class TestBudgetResolution:
    def test_defaults_apply_without_a_provider(self):
        executor = AWSCallExecutor(MagicMock(), handler_type="x")
        assert executor.retry_attempts() == (3, 2)

    def test_unusable_provider_values_fall_back_to_defaults(self):
        executor = AWSCallExecutor(
            MagicMock(), handler_type="x", attempts_provider=lambda: (MagicMock(), None)
        )  # type: ignore[arg-type, return-value]
        assert executor.retry_attempts() == (3, 2)

    def test_configured_budget_is_used(self):
        executor = AWSCallExecutor(MagicMock(), handler_type="x", attempts_provider=lambda: (5, 0))
        assert executor.strategy_config("standard", "create_tags")["max_attempts"] == 5
        assert executor.strategy_config("read_only", None)["max_attempts"] == 0

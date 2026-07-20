"""Unit tests for AWSOperations.

All AWS client calls are mocked — no real AWS connections are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from orb.infrastructure.resilience.exceptions import CircuitBreakerOpenError
from orb.providers.aws.exceptions.aws_exceptions import (
    AWSEntityNotFoundError,
    AWSInfrastructureError,
    AWSValidationError,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ops() -> AWSOperations:
    """Create an AWSOperations instance with all dependencies mocked."""
    aws_client = MagicMock()
    logger = MagicMock()
    ops = AWSOperations(aws_client=aws_client, logger=logger)
    return ops


def _client_error(code: str, message: str = "some message") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Op")


# ---------------------------------------------------------------------------
# terminate_instances_with_fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTerminateInstancesWithFallback:
    def test_empty_ids_returns_empty_result(self):
        ops = _make_ops()
        result = ops.terminate_instances_with_fallback([])
        assert result == {"terminated_instances": []}

    def test_uses_request_adapter_when_provided(self):
        ops = _make_ops()
        adapter = MagicMock()
        adapter.terminate_instances.return_value = {"terminated_instances": ["i-111"]}
        result = ops.terminate_instances_with_fallback(["i-111"], request_adapter=adapter)
        adapter.terminate_instances.assert_called_once_with(["i-111"])
        assert result == {"terminated_instances": ["i-111"]}

    def test_falls_back_to_ec2_client_without_adapter(self):
        ops = _make_ops()
        retry = MagicMock(return_value={"TerminatingInstances": []})
        ops.set_retry_method(retry)
        ops.terminate_instances_with_fallback(["i-abc"])
        retry.assert_called_once()

    def test_raises_when_retry_not_set_and_no_adapter(self):
        ops = _make_ops()
        with pytest.raises(ValueError, match="Retry method not set"):
            ops.terminate_instances_with_fallback(["i-abc"])

    def test_invalid_instance_id_returns_empty(self):
        ops = _make_ops()
        retry = MagicMock(side_effect=_client_error("InvalidInstanceID.NotFound"))
        ops.set_retry_method(retry)
        result = ops.terminate_instances_with_fallback(["i-gone"])
        assert result == {"terminated_instances": []}

    def test_other_client_error_is_reraised(self):
        ops = _make_ops()
        retry = MagicMock(side_effect=_client_error("AccessDenied"))
        ops.set_retry_method(retry)
        with pytest.raises(ClientError):
            ops.terminate_instances_with_fallback(["i-abc"])

    def test_generic_exception_is_reraised(self):
        ops = _make_ops()
        retry = MagicMock(side_effect=RuntimeError("boom"))
        ops.set_retry_method(retry)
        with pytest.raises(RuntimeError):
            ops.terminate_instances_with_fallback(["i-abc"])


# ---------------------------------------------------------------------------
# execute_operation_with_standard_handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteOperationWithStandardHandling:
    def test_successful_operation_returns_result(self):
        ops = _make_ops()
        retry = MagicMock(return_value={"ok": True})
        ops.set_retry_method(retry)
        result = ops.execute_operation_with_standard_handling(MagicMock(), "describe-things")
        assert result == {"ok": True}

    def test_raises_when_retry_not_set(self):
        """ValueError inside execute_operation_with_standard_handling is wrapped
        in AWSInfrastructureError by the except-Exception branch."""
        ops = _make_ops()
        with pytest.raises(AWSInfrastructureError):
            ops.execute_operation_with_standard_handling(MagicMock(), "op")

    def test_circuit_breaker_error_is_reraised(self):
        ops = _make_ops()
        cb_error = CircuitBreakerOpenError(
            service_name="ec2", failure_count=5, last_failure_time=0.0
        )
        retry = MagicMock(side_effect=cb_error)
        ops.set_retry_method(retry)
        with pytest.raises(CircuitBreakerOpenError):
            ops.execute_operation_with_standard_handling(MagicMock(), "op")

    def test_client_error_is_reraised_as_is(self):
        ops = _make_ops()
        retry = MagicMock(side_effect=_client_error("ValidationException"))
        ops.set_retry_method(retry)
        with pytest.raises(ClientError):
            ops.execute_operation_with_standard_handling(MagicMock(), "op")

    def test_generic_exception_wrapped_in_aws_infrastructure_error(self):
        ops = _make_ops()
        retry = MagicMock(side_effect=RuntimeError("unexpected"))
        ops.set_retry_method(retry)
        with pytest.raises(AWSInfrastructureError):
            ops.execute_operation_with_standard_handling(MagicMock(), "op")


# ---------------------------------------------------------------------------
# _convert_client_error
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConvertClientError:
    def test_invalid_parameter_value_returns_validation_error(self):
        ops = _make_ops()
        err = _client_error("InvalidParameterValue")
        result = ops._convert_client_error(err, "some-op")
        assert isinstance(result, AWSValidationError)

    def test_resource_not_found_returns_entity_not_found(self):
        ops = _make_ops()
        err = _client_error("ResourceNotFound")
        result = ops._convert_client_error(err, "some-op")
        assert isinstance(result, AWSEntityNotFoundError)

    def test_invalid_instance_id_not_found_returns_entity_not_found(self):
        ops = _make_ops()
        err = _client_error("InvalidInstanceID.NotFound")
        result = ops._convert_client_error(err, "some-op")
        assert isinstance(result, AWSEntityNotFoundError)

    def test_throttling_returns_infrastructure_error(self):
        ops = _make_ops()
        err = _client_error("Throttling")
        result = ops._convert_client_error(err, "some-op")
        assert isinstance(result, AWSInfrastructureError)

    def test_unauthorized_operation_returns_infrastructure_error(self):
        ops = _make_ops()
        err = _client_error("UnauthorizedOperation")
        result = ops._convert_client_error(err, "some-op")
        assert isinstance(result, AWSInfrastructureError)

    def test_unknown_error_code_returns_infrastructure_error(self):
        ops = _make_ops()
        err = _client_error("SomeOtherCode")
        result = ops._convert_client_error(err, "some-op")
        assert isinstance(result, AWSInfrastructureError)

    def test_error_message_included_in_result(self):
        ops = _make_ops()
        err = _client_error("InvalidParameterValue", "bad value given")
        result = ops._convert_client_error(err, "create-fleet")
        assert "bad value given" in str(result)


# ---------------------------------------------------------------------------
# log_operation_* methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogOperationMethods:
    def test_log_operation_start_with_resource_id(self):
        ops = _make_ops()
        ops.log_operation_start("create", "EC2Fleet", resource_id="fleet-123")
        ops._logger.info.assert_called()  # type: ignore[attr-defined]

    def test_log_operation_start_without_resource_id(self):
        ops = _make_ops()
        ops.log_operation_start("list", "EC2Fleet")
        ops._logger.info.assert_called()  # type: ignore[attr-defined]

    def test_log_operation_start_with_context(self):
        ops = _make_ops()
        ops.log_operation_start("describe", "ASG", extra_key="val")
        ops._logger.debug.assert_called()  # type: ignore[attr-defined]

    def test_log_operation_success(self):
        ops = _make_ops()
        ops.log_operation_success("delete", "EC2Fleet", "fleet-abc")
        ops._logger.info.assert_called()  # type: ignore[attr-defined]

    def test_log_operation_failure_with_resource_id(self):
        ops = _make_ops()
        ops.log_operation_failure("terminate", "instance", RuntimeError("boom"), "i-1")
        ops._logger.error.assert_called()  # type: ignore[attr-defined]

    def test_log_operation_failure_without_resource_id(self):
        ops = _make_ops()
        ops.log_operation_failure("terminate", "instance", RuntimeError("boom"))
        ops._logger.error.assert_called()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# get_resource_instances
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetResourceInstances:
    def test_returns_instance_ids_from_dict_list(self):
        ops = _make_ops()
        retry = MagicMock(
            return_value={"ActiveInstances": [{"InstanceId": "i-a"}, {"InstanceId": "i-b"}]}
        )
        ops.set_retry_method(retry)
        result = ops.get_resource_instances(
            "EC2Fleet", "fleet-1", MagicMock(), "ActiveInstances", FleetId="fleet-1"
        )
        assert result == ["i-a", "i-b"]

    def test_returns_string_instances_directly(self):
        ops = _make_ops()
        retry = MagicMock(return_value={"Instances": ["i-x", "i-y"]})
        ops.set_retry_method(retry)
        result = ops.get_resource_instances("Fleet", "f-1", MagicMock(), "Instances")
        assert result == ["i-x", "i-y"]

    def test_returns_empty_list_on_exception(self):
        ops = _make_ops()
        retry = MagicMock(side_effect=RuntimeError("fail"))
        ops.set_retry_method(retry)
        result = ops.get_resource_instances("Fleet", "f-1", MagicMock(), "Instances")
        assert result == []

    def test_returns_empty_list_when_retry_not_set(self):
        """get_resource_instances catches all exceptions and returns []."""
        ops = _make_ops()
        result = ops.get_resource_instances("Fleet", "f-1", MagicMock(), "Instances")
        assert result == []


# ---------------------------------------------------------------------------
# _get_package_name
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPackageName:
    def test_returns_package_name_from_config_port(self):
        ops = _make_ops()
        config_port = MagicMock()
        config_port.get_package_info.return_value = {"name": "my-broker"}
        ops._config_port = config_port
        assert ops._get_package_name() == "my-broker"

    def test_returns_default_when_no_config_port(self):
        ops = _make_ops()
        ops._config_port = None
        assert ops._get_package_name() == "open-resource-broker"

    def test_returns_default_when_config_port_raises(self):
        ops = _make_ops()
        config_port = MagicMock()
        config_port.get_package_info.side_effect = RuntimeError("error")
        ops._config_port = config_port
        assert ops._get_package_name() == "open-resource-broker"

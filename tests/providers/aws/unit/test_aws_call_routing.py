"""Instance operations and adapters retry transient AWS failures.

Each call below used to reach EC2 directly, so one throttle surfaced as a
failure. Permanent errors must still be attempted once, and the result text
reported for them must not change.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from orb.providers.aws.infrastructure.adapters.request_adapter import AWSRequestAdapter
from orb.providers.aws.services.instance_operation_service import AWSInstanceOperationService
from orb.providers.base.strategy.provider_strategy import ProviderOperation, ProviderOperationType

pytestmark = pytest.mark.unit

SLEEP = "orb.infrastructure.resilience.retry_decorator.time.sleep"


def _error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": f"{code} message"}}, "Op")


def _service() -> AWSInstanceOperationService:
    return AWSInstanceOperationService(
        aws_client=MagicMock(),
        logger=MagicMock(),
        provisioning_adapter=None,  # type: ignore[arg-type]
        machine_adapter=MagicMock(),
    )


def _op(kind: ProviderOperationType, **params) -> ProviderOperation:
    return ProviderOperation(operation_type=kind, parameters=params)


class TestInstanceOperationService:
    def test_terminate_fallback_retries_a_throttle(self):
        service = _service()
        ec2 = service._aws_client.ec2_client
        ec2.terminate_instances.side_effect = [
            _error("RequestLimitExceeded"),
            {"TerminatingInstances": [{"InstanceId": "i-1"}]},
        ]
        with patch(SLEEP):
            result = service.terminate_instances(
                _op(ProviderOperationType.TERMINATE_INSTANCES, instance_ids=["i-1"])
            )
        assert result.success
        assert result.data["terminated_count"] == 1
        assert ec2.terminate_instances.call_count == 2

    def test_terminate_fallback_permanent_error_is_attempted_once_with_the_same_text(self):
        service = _service()
        ec2 = service._aws_client.ec2_client
        ec2.terminate_instances.side_effect = _error("UnauthorizedOperation")
        with patch(SLEEP) as sleep:
            result = service.terminate_instances(
                _op(ProviderOperationType.TERMINATE_INSTANCES, instance_ids=["i-1"])
            )
        assert not result.success
        assert ec2.terminate_instances.call_count == 1
        assert sleep.call_count == 0
        assert result.error_message == (
            "Failed to terminate instances: An error occurred (UnauthorizedOperation) "
            "when calling the Op operation: UnauthorizedOperation message"
        )

    def test_status_describe_retries_a_throttle(self):
        service = _service()
        ec2 = service._aws_client.ec2_client
        ec2.describe_instances.side_effect = [
            _error("Throttling"),
            {"Reservations": [{"Instances": [{"InstanceId": "i-1"}]}]},
        ]
        service._machine_adapter.create_machine_from_aws_instance.return_value = {"id": "i-1"}
        with patch(SLEEP):
            result = service.get_instance_status(
                _op(ProviderOperationType.GET_INSTANCE_STATUS, instance_ids=["i-1"])
            )
        assert result.success
        assert result.data["instances"] == [{"id": "i-1"}]
        assert ec2.describe_instances.call_count == 2

    def test_status_describe_of_a_purged_instance_still_settles(self):
        service = _service()
        ec2 = service._aws_client.ec2_client
        not_found = ClientError(
            {
                "Error": {
                    "Code": "InvalidInstanceID.NotFound",
                    "Message": "The instance ID 'i-1' does not exist",
                }
            },
            "DescribeInstances",
        )
        ec2.describe_instances.side_effect = not_found
        with patch(SLEEP):
            result = service.get_instance_status(
                _op(ProviderOperationType.GET_INSTANCE_STATUS, instance_ids=["i-1"])
            )
        assert result.success
        assert result.data["instances"] == []

    @pytest.mark.parametrize(
        "method, kind",
        [
            ("start_instances", ProviderOperationType.START_INSTANCES),
            ("stop_instances", ProviderOperationType.STOP_INSTANCES),
        ],
    )
    def test_start_and_stop_retry_a_throttle(self, method, kind):
        service = _service()
        ec2 = service._aws_client.ec2_client
        getattr(ec2, method).side_effect = [_error("RequestLimitExceeded"), {}]
        with patch(SLEEP):
            result = getattr(service, method)(_op(kind, instance_ids=["i-1"]))
        assert result.success
        assert getattr(ec2, method).call_count == 2

    @pytest.mark.parametrize(
        "method, kind",
        [
            ("start_instances", ProviderOperationType.START_INSTANCES),
            ("stop_instances", ProviderOperationType.STOP_INSTANCES),
        ],
    )
    def test_start_and_stop_permanent_error_is_attempted_once(self, method, kind):
        service = _service()
        ec2 = service._aws_client.ec2_client
        getattr(ec2, method).side_effect = _error("IncorrectInstanceState")
        with patch(SLEEP) as sleep:
            result = getattr(service, method)(_op(kind, instance_ids=["i-1"]))
        assert not result.success
        assert getattr(ec2, method).call_count == 1
        assert sleep.call_count == 0


class TestRequestAdapter:
    def _adapter(self) -> AWSRequestAdapter:
        return AWSRequestAdapter(aws_client=MagicMock(), logger=MagicMock())

    def test_terminate_instances_retries_a_throttle(self):
        adapter = self._adapter()
        ec2 = adapter._aws_client.ec2_client
        ec2.terminate_instances.side_effect = [
            _error("RequestLimitExceeded"),
            {
                "TerminatingInstances": [
                    {
                        "InstanceId": "i-1",
                        "PreviousState": {"Name": "running"},
                        "CurrentState": {"Name": "shutting-down"},
                    }
                ]
            },
        ]
        with patch(SLEEP):
            result = adapter.terminate_instances(["i-1"])
        assert result["status"] == "success"
        assert ec2.terminate_instances.call_count == 2

    def test_terminate_instances_not_found_is_success_and_not_retried(self):
        adapter = self._adapter()
        ec2 = adapter._aws_client.ec2_client
        ec2.terminate_instances.side_effect = _error("InvalidInstanceID.NotFound")
        with patch(SLEEP) as sleep:
            result = adapter.terminate_instances(["i-1"])
        assert result["status"] == "success"
        assert ec2.terminate_instances.call_count == 1
        assert sleep.call_count == 0

    def test_terminate_instances_permanent_error_keeps_the_error_dict_contract(self):
        adapter = self._adapter()
        ec2 = adapter._aws_client.ec2_client
        ec2.terminate_instances.side_effect = _error("UnauthorizedOperation")
        result = adapter.terminate_instances(["i-1"])
        assert result["status"] == "error"
        assert "UnauthorizedOperation" in result["message"]
        assert ec2.terminate_instances.call_count == 1

    def test_status_describes_retry_a_throttle(self):
        adapter = self._adapter()
        ec2 = adapter._aws_client.ec2_client
        ec2.describe_fleets.side_effect = [
            _error("RequestLimitExceeded"),
            {
                "Fleets": [
                    {
                        "FleetState": "active",
                        "TargetCapacitySpecification": {"TotalTargetCapacity": 1},
                    }
                ]
            },
        ]
        ec2.describe_fleet_instances.return_value = {"ActiveInstances": []}
        request = MagicMock()
        request.resource_id = "fleet-1"
        with patch(SLEEP):
            result = adapter._get_ec2_fleet_status(request)
        assert result["status"] == "active"
        assert ec2.describe_fleets.call_count == 2

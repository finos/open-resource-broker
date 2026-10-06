"""Unit tests for AWSHandler._get_instance_details retry behavior.

Every handler's poll-cycle status check routes through this method, so a
transient throttling error on describe_instances must be retried the same
way the handlers' other describe/create calls are retried.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from orb.providers.aws.exceptions.aws_exceptions import InfrastructureError
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler


def _make_handler() -> EC2FleetHandler:
    aws_client = MagicMock()
    aws_client.region_name = "us-east-1"
    logger = MagicMock()
    aws_ops = MagicMock()
    launch_template_manager = MagicMock()
    handler = EC2FleetHandler(aws_client, logger, aws_ops, launch_template_manager)
    handler._machine_adapter = None
    return handler


def _throttle_error() -> ClientError:
    return ClientError(
        {"Error": {"Code": "RequestLimitExceeded", "Message": "slow down"}},
        "DescribeInstances",
    )


_DESCRIBE_RESPONSE = {
    "Reservations": [
        {
            "Instances": [
                {
                    "InstanceId": "i-123",
                    "InstanceType": "t3.medium",
                    "State": {"Name": "running"},
                    "Placement": {"AvailabilityZone": "us-east-1b"},
                    "PrivateIpAddress": "10.0.1.1",
                    "SecurityGroups": [],
                }
            ]
        }
    ]
}


@pytest.mark.unit
class TestGetInstanceDetailsRetry:
    def test_retries_on_throttling_then_succeeds(self):
        handler = _make_handler()
        calls = {"n": 0}

        def flaky(InstanceIds):  # noqa: N803 - mirrors the boto3 kwarg casing
            calls["n"] += 1
            if calls["n"] < 2:
                raise _throttle_error()
            return _DESCRIBE_RESPONSE

        handler.aws_client.ec2_client.describe_instances.side_effect = flaky

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep") as sleep:
            result = handler._get_instance_details(["i-123"], provider_api="EC2Fleet")

        assert calls["n"] == 2
        assert sleep.call_count == 1
        assert len(result) == 1
        assert result[0]["name"] == "i-123"

    def test_exhausts_retries_and_raises_on_persistent_throttling(self):
        handler = _make_handler()
        handler.aws_client.ec2_client.describe_instances.side_effect = _throttle_error()

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"):
            with pytest.raises(InfrastructureError):
                handler._get_instance_details(["i-123"], provider_api="EC2Fleet")

        # read_only strategy allows one retry on top of the initial attempt.
        assert handler.aws_client.ec2_client.describe_instances.call_count == 3

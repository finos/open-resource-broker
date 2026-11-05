"""An ASG request is fulfilled only when its InService instances are running in EC2."""

from unittest.mock import Mock

import pytest

from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler


def _handler() -> ASGHandler:
    return ASGHandler(
        aws_client=Mock(), logger=Mock(), aws_ops=Mock(), launch_template_manager=Mock()
    )


def _fulfilment(ec2_statuses: list[str], desired: int = 2):
    return _handler()._compute_asg_fulfilment(
        desired_capacity=desired,
        in_service_weighted=desired,
        pending_count=0,
        in_service_count=desired,
        ec2_instances=[{"status": status} for status in ec2_statuses],
    )


@pytest.mark.unit
class TestAsgFulfilmentRequiresRunning:
    def test_in_service_instances_still_pending_in_ec2_are_in_progress(self):
        result = _fulfilment(["running", "pending"])

        assert result.state == "in_progress"
        assert result.running_count == 1

    def test_unknown_ec2_state_is_in_progress(self):
        assert _fulfilment([]).state == "in_progress"

    def test_all_in_service_instances_running_is_fulfilled(self):
        result = _fulfilment(["running", "running"])

        assert result.state == "fulfilled"
        assert result.running_count == 2

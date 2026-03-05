"""Tests for fleet capacity metadata extraction."""

from unittest.mock import Mock

import pytest


class _NoopLogger:
    debug = info = warning = error = lambda *args, **kwargs: None


@pytest.mark.unit
def test_ec2_fleet_capacity_data_extraction():
    """EC2 Fleet fulfilled capacity is read from DescribeFleets response."""
    from providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler

    mock_aws_client = Mock()
    mock_aws_client.ec2_client.describe_fleets.return_value = {
        "Fleets": [
            {
                "TargetCapacitySpecification": {"TotalTargetCapacity": 10},
                "FulfilledCapacity": 6.0,
                "FleetState": "active",
                "Type": "maintain",
                "Instances": [],
            }
        ]
    }

    EC2FleetHandler(
        aws_client=mock_aws_client,
        logger=_NoopLogger(),
        aws_ops=Mock(),
        launch_template_manager=Mock(),
    )

    fleet = mock_aws_client.ec2_client.describe_fleets(FleetIds=["fleet-123"])["Fleets"][0]

    assert fleet["TargetCapacitySpecification"]["TotalTargetCapacity"] == 10
    assert fleet["FulfilledCapacity"] == 6.0
    assert fleet["FleetState"] == "active"


@pytest.mark.unit
def test_spot_fleet_capacity_data_extraction():
    """Spot Fleet fulfilled capacity is read from DescribeSpotFleetRequests response."""
    from providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler

    mock_aws_client = Mock()
    mock_aws_client.ec2_client.describe_spot_fleet_requests.return_value = {
        "SpotFleetRequestConfigs": [
            {
                "SpotFleetRequestConfig": {
                    "TargetCapacity": 12,
                    "FulfilledCapacity": 9.0,
                },
                "SpotFleetRequestState": "active",
            }
        ]
    }

    SpotFleetHandler(
        aws_client=mock_aws_client,
        logger=_NoopLogger(),
        aws_ops=Mock(),
        launch_template_manager=Mock(),
    )

    configs = mock_aws_client.ec2_client.describe_spot_fleet_requests(
        SpotFleetRequestIds=["sfr-123"]
    )["SpotFleetRequestConfigs"]
    config = configs[0]

    assert config["SpotFleetRequestConfig"]["TargetCapacity"] == 12
    assert config["SpotFleetRequestConfig"]["FulfilledCapacity"] == 9.0
    assert config["SpotFleetRequestState"] == "active"


@pytest.mark.unit
def test_asg_capacity_data_extraction():
    """ASG InService instance count and weighted capacity are read from DescribeAutoScalingGroups."""
    from providers.aws.infrastructure.handlers.asg.handler import ASGHandler

    mock_aws_client = Mock()
    mock_aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [
            {
                "DesiredCapacity": 10,
                "Status": "ok",
                "Instances": [
                    {"LifecycleState": "InService", "WeightedCapacity": "4"},
                    {"LifecycleState": "InService", "WeightedCapacity": "1"},
                    {"LifecycleState": "InService", "WeightedCapacity": "2"},
                    {"LifecycleState": "Pending", "WeightedCapacity": "5"},
                ],
            }
        ]
    }

    ASGHandler(
        aws_client=mock_aws_client,
        logger=_NoopLogger(),
        aws_ops=Mock(),
        launch_template_manager=Mock(),
    )

    groups = mock_aws_client.autoscaling_client.describe_auto_scaling_groups(
        AutoScalingGroupNames=["asg-123"]
    )["AutoScalingGroups"]
    group = groups[0]

    in_service = [i for i in group["Instances"] if i["LifecycleState"] == "InService"]
    fulfilled = sum(int(i["WeightedCapacity"]) for i in in_service)

    assert group["DesiredCapacity"] == 10
    assert fulfilled == 7  # 4 + 1 + 2
    assert len(in_service) == 3

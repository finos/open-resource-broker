import types

import pytest

from providers.aws.configuration.config import AWSProviderConfig
from providers.aws.domain.template.value_objects import ProviderApi
from providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy


class _NoopLogger:
    debug = info = warning = error = lambda *args, **kwargs: None


def _strategy_with_clients(ec2_client, autoscaling_client):
    cfg = AWSProviderConfig(
        access_key_id="test",
        secret_access_key="test",
        region="us-east-1",
    )
    strategy = AWSProviderStrategy(config=cfg, logger=_NoopLogger())
    # Inject stubbed AWS client to avoid real AWS calls
    strategy._aws_client = types.SimpleNamespace(
        ec2_client=ec2_client, autoscaling_client=autoscaling_client
    )
    return strategy


@pytest.mark.unit
def test_augment_capacity_metadata_ec2_fleet_mixed_weights():
    """EC2 Fleet uses fulfilled capacity from API (already weight-adjusted)."""
    ec2_client = types.SimpleNamespace(
        describe_fleets=lambda FleetIds: {
            "Fleets": [
                {
                    "TargetCapacitySpecification": {"TotalTargetCapacity": 10},
                    "FulfilledCapacity": 6,  # e.g., 2 small (1 each) + 1 large (4)
                    "FleetState": "active",
                }
            ]
        }
    )
    autoscaling_client = types.SimpleNamespace()
    strategy = _strategy_with_clients(ec2_client, autoscaling_client)

    metadata = {}
    strategy._augment_capacity_metadata(metadata, ProviderApi.EC2_FLEET, ["fleet-123"])

    assert metadata["fleet_capacity_fulfilment"] == {
        "target_capacity_units": 10,
        "fulfilled_capacity_units": 6,
        "provisioned_instance_count": 6,
        "state": "active",
    }


@pytest.mark.unit
def test_augment_capacity_metadata_spot_fleet_mixed_weights():
    """Spot Fleet fulfilled capacity comes from API (sum of weighted overrides)."""
    ec2_client = types.SimpleNamespace(
        describe_spot_fleet_requests=lambda SpotFleetRequestIds: {
            "SpotFleetRequestConfigs": [
                {
                    "SpotFleetRequestConfig": {
                        "TargetCapacity": 12,
                        "FulfilledCapacity": 9,  # e.g., mix of weighted overrides
                    },
                    "SpotFleetRequestState": "active",
                }
            ]
        }
    )
    autoscaling_client = types.SimpleNamespace()
    strategy = _strategy_with_clients(ec2_client, autoscaling_client)

    metadata = {}
    strategy._augment_capacity_metadata(metadata, ProviderApi.SPOT_FLEET, ["sfr-123"])

    assert metadata["fleet_capacity_fulfilment"] == {
        "target_capacity_units": 12,
        "fulfilled_capacity_units": 9,
        "provisioned_instance_count": 9,
        "state": "active",
    }


@pytest.mark.unit
def test_augment_capacity_metadata_asg_mixed_weights():
    """ASG fulfilled capacity sums weighted instances; count reflects InService instances."""
    autoscaling_client = types.SimpleNamespace(
        describe_auto_scaling_groups=lambda AutoScalingGroupNames: {
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
    )
    ec2_client = types.SimpleNamespace()
    strategy = _strategy_with_clients(ec2_client, autoscaling_client)

    metadata = {}
    strategy._augment_capacity_metadata(metadata, ProviderApi.ASG, ["asg-123"])

    assert metadata["fleet_capacity_fulfilment"] == {
        "target_capacity_units": 10,
        "fulfilled_capacity_units": 7,  # 4 + 1 + 2 (only InService)
        "provisioned_instance_count": 3,  # three InService instances
        "state": "ok",
    }

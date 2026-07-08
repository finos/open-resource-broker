"""Unit tests for FleetCapacityFulfilment dataclass and the three fetcher methods.

Covers:
- FleetCapacityFulfilment frozen dataclass construction and immutability
- EC2FleetHandler._fetch_ec2_fleet_capacity under sample DescribeFleets responses
- SpotFleetHandler._fetch_spot_fleet_capacity under sample DescribeSpotFleetRequests responses
- ASGHandler._fetch_asg_capacity under sample DescribeAutoScalingGroups responses
"""

from unittest.mock import Mock

import pytest

try:
    from orb.providers.aws.aws_fleet_capacity import FleetCapacityFulfilment
    from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
    from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
    from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler

    IMPORTS_AVAILABLE = True
except ImportError:
    IMPORTS_AVAILABLE = False

pytestmark = pytest.mark.skipif(not IMPORTS_AVAILABLE, reason="AWS provider not installed")


# ---------------------------------------------------------------------------
# FleetCapacityFulfilment dataclass
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFleetCapacityFulfilmentDataclass:
    """FleetCapacityFulfilment must be a frozen dataclass with the correct fields."""

    def test_construction_with_all_fields(self):
        fc = FleetCapacityFulfilment(
            target_capacity_units=10,
            fulfilled_capacity_units=8,
            provisioned_instance_count=8,
            fulfillment_complete=False,
        )
        assert fc.target_capacity_units == 10
        assert fc.fulfilled_capacity_units == 8
        assert fc.provisioned_instance_count == 8
        assert fc.fulfillment_complete is False

    def test_construction_target_none(self):
        """target_capacity_units may be None when AWS omits the field."""
        fc = FleetCapacityFulfilment(
            target_capacity_units=None,
            fulfilled_capacity_units=0,
            provisioned_instance_count=0,
            fulfillment_complete=False,
        )
        assert fc.target_capacity_units is None

    def test_fulfillment_complete_true(self):
        fc = FleetCapacityFulfilment(
            target_capacity_units=5,
            fulfilled_capacity_units=5,
            provisioned_instance_count=5,
            fulfillment_complete=True,
        )
        assert fc.fulfillment_complete is True

    def test_frozen_rejects_mutation(self):
        """Frozen dataclass must raise FrozenInstanceError on assignment."""
        from dataclasses import FrozenInstanceError

        fc = FleetCapacityFulfilment(
            target_capacity_units=2,
            fulfilled_capacity_units=2,
            provisioned_instance_count=2,
            fulfillment_complete=True,
        )
        with pytest.raises(FrozenInstanceError):
            fc.target_capacity_units = 99  # type: ignore[misc]

    def test_equality(self):
        a = FleetCapacityFulfilment(
            target_capacity_units=4,
            fulfilled_capacity_units=2,
            provisioned_instance_count=2,
            fulfillment_complete=False,
        )
        b = FleetCapacityFulfilment(
            target_capacity_units=4,
            fulfilled_capacity_units=2,
            provisioned_instance_count=2,
            fulfillment_complete=False,
        )
        assert a == b


# ---------------------------------------------------------------------------
# EC2FleetHandler._fetch_ec2_fleet_capacity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchEC2FleetCapacity:
    """EC2FleetHandler._fetch_ec2_fleet_capacity must return correct FleetCapacityFulfilment."""

    def _handler(self) -> EC2FleetHandler:
        config_port = Mock()
        config_port.get_resource_prefix.return_value = ""
        return EC2FleetHandler(Mock(), Mock(), Mock(), Mock(), config_port=config_port)

    def test_partial_fulfillment(self):
        fleet = {
            "TargetCapacitySpecification": {"TotalTargetCapacity": 10},
            "FulfilledCapacity": 6.0,
        }
        fc = EC2FleetHandler._fetch_ec2_fleet_capacity(fleet, active_instance_count=6)
        assert fc.target_capacity_units == 10
        assert fc.fulfilled_capacity_units == 6
        assert fc.provisioned_instance_count == 6
        assert fc.fulfillment_complete is False

    def test_full_fulfillment(self):
        fleet = {
            "TargetCapacitySpecification": {"TotalTargetCapacity": 5},
            "FulfilledCapacity": 5.0,
        }
        fc = EC2FleetHandler._fetch_ec2_fleet_capacity(fleet, active_instance_count=5)
        assert fc.target_capacity_units == 5
        assert fc.fulfilled_capacity_units == 5
        assert fc.fulfillment_complete is True

    def test_missing_fulfilled_capacity_defaults_to_zero(self):
        fleet = {
            "TargetCapacitySpecification": {"TotalTargetCapacity": 3},
        }
        fc = EC2FleetHandler._fetch_ec2_fleet_capacity(fleet)
        assert fc.fulfilled_capacity_units == 0
        assert fc.fulfillment_complete is False

    def test_missing_target_capacity_specification(self):
        fleet = {"FulfilledCapacity": 2.0}
        fc = EC2FleetHandler._fetch_ec2_fleet_capacity(fleet)
        assert fc.target_capacity_units is None
        assert fc.fulfilled_capacity_units == 2
        assert fc.fulfillment_complete is False

    def test_over_fulfillment_is_complete(self):
        """fulfilled > target still counts as fulfillment_complete=True."""
        fleet = {
            "TargetCapacitySpecification": {"TotalTargetCapacity": 3},
            "FulfilledCapacity": 4.0,
        }
        fc = EC2FleetHandler._fetch_ec2_fleet_capacity(fleet)
        assert fc.fulfillment_complete is True


# ---------------------------------------------------------------------------
# SpotFleetHandler._fetch_spot_fleet_capacity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchSpotFleetCapacity:
    """SpotFleetHandler._fetch_spot_fleet_capacity must return correct FleetCapacityFulfilment."""

    def _config_entry(
        self,
        target: int | None = 10,
        fulfilled: float = 0.0,
    ) -> dict:
        cfg: dict = {}
        if target is not None:
            cfg["TargetCapacity"] = target
        cfg["FulfilledCapacity"] = fulfilled
        return {"SpotFleetRequestConfig": cfg}

    def test_partial_fulfillment(self):
        entry = self._config_entry(target=10, fulfilled=4.0)
        fc = SpotFleetHandler._fetch_spot_fleet_capacity(entry, active_instance_count=4)
        assert fc.target_capacity_units == 10
        assert fc.fulfilled_capacity_units == 4
        assert fc.provisioned_instance_count == 4
        assert fc.fulfillment_complete is False

    def test_full_fulfillment(self):
        entry = self._config_entry(target=5, fulfilled=5.0)
        fc = SpotFleetHandler._fetch_spot_fleet_capacity(entry, active_instance_count=5)
        assert fc.target_capacity_units == 5
        assert fc.fulfilled_capacity_units == 5
        assert fc.fulfillment_complete is True

    def test_no_target_capacity(self):
        """TargetCapacity absent → target_capacity_units=None, fulfillment_complete=False."""
        entry = {"SpotFleetRequestConfig": {"FulfilledCapacity": 2.0}}
        fc = SpotFleetHandler._fetch_spot_fleet_capacity(entry, active_instance_count=2)
        assert fc.target_capacity_units is None
        assert fc.fulfilled_capacity_units == 2
        assert fc.fulfillment_complete is False

    def test_zero_instances(self):
        entry = self._config_entry(target=3, fulfilled=0.0)
        fc = SpotFleetHandler._fetch_spot_fleet_capacity(entry, active_instance_count=0)
        assert fc.provisioned_instance_count == 0
        assert fc.fulfillment_complete is False

    def test_float_fulfilled_truncated_to_int(self):
        entry = self._config_entry(target=10, fulfilled=9.9)
        fc = SpotFleetHandler._fetch_spot_fleet_capacity(entry, active_instance_count=9)
        assert fc.fulfilled_capacity_units == 9


# ---------------------------------------------------------------------------
# ASGHandler._fetch_asg_capacity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchASGCapacity:
    """ASGHandler._fetch_asg_capacity must return correct FleetCapacityFulfilment."""

    def _group(
        self,
        desired: int,
        instances: list[dict],
    ) -> dict:
        return {"DesiredCapacity": desired, "Instances": instances}

    def _instance(self, state: str = "InService", weight: int | None = None) -> dict:
        inst: dict = {"InstanceId": "i-001", "LifecycleState": state}
        if weight is not None:
            inst["WeightedCapacity"] = str(weight)
        return inst

    def test_all_in_service_unweighted(self):
        group = self._group(3, [self._instance("InService")] * 3)
        fc = ASGHandler._fetch_asg_capacity(group)
        assert fc.target_capacity_units == 3
        assert fc.fulfilled_capacity_units == 3  # 3 × 1 unweighted
        assert fc.provisioned_instance_count == 3
        assert fc.fulfillment_complete is True

    def test_partial_in_service(self):
        group = self._group(5, [self._instance("InService")] * 2 + [self._instance("Pending")])
        fc = ASGHandler._fetch_asg_capacity(group)
        assert fc.target_capacity_units == 5
        assert fc.fulfilled_capacity_units == 2  # only InService contribute
        assert fc.provisioned_instance_count == 2
        assert fc.fulfillment_complete is False

    def test_weighted_instances(self):
        group = self._group(
            10,
            [
                self._instance("InService", weight=4),
                self._instance("InService", weight=4),
                self._instance("InService", weight=4),
            ],
        )
        fc = ASGHandler._fetch_asg_capacity(group)
        assert fc.fulfilled_capacity_units == 12  # 3 × 4
        assert fc.fulfillment_complete is True  # 12 >= 10

    def test_empty_instances(self):
        group = self._group(2, [])
        fc = ASGHandler._fetch_asg_capacity(group)
        assert fc.fulfilled_capacity_units == 0
        assert fc.provisioned_instance_count == 0
        assert fc.fulfillment_complete is False

    def test_zero_desired_capacity(self):
        group = self._group(0, [])
        fc = ASGHandler._fetch_asg_capacity(group)
        assert fc.target_capacity_units == 0
        assert fc.fulfillment_complete is False  # desired=0 → not considered complete

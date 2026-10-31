"""Capacity reduction of a maintain fleet must not cause an extra termination.

moto does not emulate excess-capacity termination or the relaunch that follows
it, so these tests pin the API arguments, the order of calls and the instances
touched. The behaviour itself is only observable against AWS.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.aws.infrastructure.handlers.ec2_fleet.release_manager import (
    EC2FleetReleaseManager,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.release_manager import (
    SpotFleetReleaseManager,
)

pytestmark = pytest.mark.unit


def _retry(fn, operation_type="standard", operation_name=None, **kwargs):
    if callable(fn) and not kwargs and operation_type != "critical":
        try:
            return fn()
        except TypeError:
            pass
    return fn(**kwargs)


def _ec2_manager(ec2: Any) -> tuple[EC2FleetReleaseManager, MagicMock]:
    aws_client = MagicMock()
    aws_client.ec2_client = ec2
    aws_ops = MagicMock()
    manager = EC2FleetReleaseManager(
        aws_client=aws_client,
        aws_ops=aws_ops,
        request_adapter=None,
        config_port=None,
        logger=MagicMock(),
        retry_fn=_retry,
        paginate_fn=lambda method, key, **kw: method(**kw).get(key, []),
        collect_with_next_token_fn=lambda method, key, **kw: method(**kw).get(key, []),
        cleanup_on_zero_capacity_fn=MagicMock(),
    )
    return manager, aws_ops


def _spot_manager(ec2: Any) -> tuple[SpotFleetReleaseManager, MagicMock]:
    aws_client = MagicMock()
    aws_client.ec2_client = ec2
    aws_ops = MagicMock()
    manager = SpotFleetReleaseManager(
        aws_client=aws_client,
        aws_ops=aws_ops,
        request_adapter=None,
        cleanup_on_zero_capacity_fn=MagicMock(),
        logger=MagicMock(),
        retry_fn=_retry,
    )
    return manager, aws_ops


def _ec2_fleet(fleet_type: str, target: int) -> dict:
    return {
        "Type": fleet_type,
        "TargetCapacitySpecification": {"TotalTargetCapacity": target},
        "Tags": [{"Key": "orb:request-id", "Value": "req-1"}],
    }


def _spot_fleet(fleet_type: str, target: int, on_demand: int = 0) -> dict:
    return {
        "SpotFleetRequestConfig": {
            "Type": fleet_type,
            "TargetCapacity": target,
            "OnDemandTargetCapacity": on_demand,
        }
    }


class TestEC2FleetReduction:
    def _ec2(self, live_target: int, instance_ids: list[str]) -> MagicMock:
        ec2 = MagicMock()
        ec2.describe_fleets.return_value = {
            "Fleets": [_ec2_fleet("maintain", live_target) | {"FleetId": "fleet-1"}]
        }
        ec2.describe_fleet_instances.return_value = {
            "ActiveInstances": [{"InstanceId": i, "InstanceType": "t3.micro"} for i in instance_ids]
        }
        ec2.modify_fleet.return_value = {}
        return ec2

    def test_modify_fleet_never_terminates_excess_capacity(self):
        ec2 = self._ec2(3, ["i-1", "i-2", "i-3"])
        manager, _ = _ec2_manager(ec2)

        manager.release("fleet-1", ["i-1"], _ec2_fleet("maintain", 3))

        kwargs = ec2.modify_fleet.call_args.kwargs
        assert kwargs["ExcessCapacityTerminationPolicy"] == "no-termination"
        assert kwargs["TargetCapacitySpecification"] == {"TotalTargetCapacity": 2}

    def test_target_is_re_read_immediately_before_the_modify(self):
        # The details handed in say 3; another return already brought the live target to 2.
        ec2 = self._ec2(2, ["i-2", "i-3"])
        manager, _ = _ec2_manager(ec2)
        order: list[str] = []
        ec2.describe_fleets.side_effect = lambda **kw: (
            order.append("describe_fleets") or ec2.describe_fleets.return_value
        )
        ec2.modify_fleet.side_effect = lambda **kw: order.append("modify_fleet") or {}

        manager.release("fleet-1", ["i-2"], _ec2_fleet("maintain", 3))

        assert ec2.modify_fleet.call_args.kwargs["TargetCapacitySpecification"] == {
            "TotalTargetCapacity": 1
        }
        assert order.index("describe_fleets") < order.index("modify_fleet")

    def test_falls_back_to_the_supplied_target_when_the_re_read_fails(self):
        ec2 = self._ec2(3, ["i-1", "i-2", "i-3"])
        ec2.describe_fleets.side_effect = RuntimeError("describe failed")
        manager, _ = _ec2_manager(ec2)

        manager.release("fleet-1", ["i-1"], _ec2_fleet("maintain", 3))

        assert ec2.modify_fleet.call_args.kwargs["TargetCapacitySpecification"] == {
            "TotalTargetCapacity": 2
        }

    def test_modify_runs_before_terminate_and_only_named_instances_are_terminated(self):
        ec2 = self._ec2(3, ["i-1", "i-2", "i-3"])
        manager, aws_ops = _ec2_manager(ec2)
        order: list[str] = []
        ec2.modify_fleet.side_effect = lambda **kw: order.append("modify") or {}
        aws_ops.terminate_instances_with_fallback.side_effect = lambda ids, *a, **kw: (
            order.append(f"terminate:{ids}") or {}
        )

        manager.release("fleet-1", ["i-2"], _ec2_fleet("maintain", 3))

        assert order == ["modify", "terminate:['i-2']"]
        assert aws_ops.terminate_instances_with_fallback.call_count == 1

    @pytest.mark.parametrize("fleet_type", ["request", "instant"])
    def test_request_and_instant_fleets_never_reach_the_modify(self, fleet_type):
        ec2 = self._ec2(3, ["i-1", "i-2", "i-3"])
        manager, _ = _ec2_manager(ec2)

        manager.release("fleet-1", ["i-1"], _ec2_fleet(fleet_type, 3))

        ec2.modify_fleet.assert_not_called()

    def test_zero_capacity_before_teardown_is_unchanged(self):
        ec2 = self._ec2(1, ["i-1"])
        manager, _ = _ec2_manager(ec2)

        manager._zero_capacity("fleet-1")

        ec2.modify_fleet.assert_called_once_with(
            FleetId="fleet-1", TargetCapacitySpecification={"TotalTargetCapacity": 0}
        )


class TestSpotFleetReduction:
    def _ec2(self, live_target: int, live_on_demand: int = 0) -> MagicMock:
        ec2 = MagicMock()
        ec2.describe_spot_fleet_requests.return_value = {
            "SpotFleetRequestConfigs": [_spot_fleet("maintain", live_target, live_on_demand)]
        }
        ec2.describe_spot_fleet_instances.return_value = {
            "ActiveInstances": [
                {"InstanceId": "i-1", "InstanceType": "t3.micro"},
                {"InstanceId": "i-2", "InstanceType": "t3.micro"},
            ]
        }
        ec2.modify_spot_fleet_request.return_value = {}
        return ec2

    def test_modify_never_terminates_excess_capacity(self):
        ec2 = self._ec2(3)
        manager, _ = _spot_manager(ec2)

        manager.release("sfr-1", ["i-1"], _spot_fleet("maintain", 3))

        kwargs = ec2.modify_spot_fleet_request.call_args.kwargs
        assert kwargs["ExcessCapacityTerminationPolicy"] == "NoTermination"
        assert kwargs["TargetCapacity"] == 2

    def test_targets_are_re_read_immediately_before_the_modify(self):
        ec2 = self._ec2(live_target=2, live_on_demand=1)
        manager, _ = _spot_manager(ec2)
        order: list[str] = []
        original = ec2.describe_spot_fleet_requests.return_value
        ec2.describe_spot_fleet_requests.side_effect = lambda **kw: (
            order.append("describe") or original
        )
        ec2.modify_spot_fleet_request.side_effect = lambda **kw: order.append("modify") or {}

        manager.release("sfr-1", ["i-1"], _spot_fleet("maintain", 3, 3))

        kwargs = ec2.modify_spot_fleet_request.call_args.kwargs
        assert kwargs["TargetCapacity"] == 1
        assert kwargs["OnDemandTargetCapacity"] == 1
        assert order.index("describe") < order.index("modify")

    def test_modify_runs_before_terminate_and_only_named_instances_are_terminated(self):
        ec2 = self._ec2(3)
        manager, aws_ops = _spot_manager(ec2)
        order: list[str] = []
        ec2.modify_spot_fleet_request.side_effect = lambda **kw: order.append("modify") or {}
        aws_ops.terminate_instances_with_fallback.side_effect = lambda ids, *a, **kw: (
            order.append(f"terminate:{ids}") or {}
        )

        manager.release("sfr-1", ["i-2"], _spot_fleet("maintain", 3))

        assert order == ["modify", "terminate:['i-2']"]

    def test_request_fleets_never_reach_the_modify(self):
        ec2 = self._ec2(3)
        manager, _ = _spot_manager(ec2)

        manager.release("sfr-1", ["i-1"], _spot_fleet("request", 3))

        ec2.modify_spot_fleet_request.assert_not_called()


class TestAutoScalingGroupReductionIsAlreadyAtomic:
    def test_instances_are_detached_and_the_desired_capacity_decremented_in_one_call(self):
        import inspect

        from orb.providers.aws.infrastructure.handlers.asg import capacity_manager

        source = inspect.getsource(capacity_manager)
        assert "terminate_instance_in_auto_scaling_group" in source
        assert "ShouldDecrementDesiredCapacity=True" in source

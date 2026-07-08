"""Unit tests for weighted-capacity arithmetic in EC2Fleet and SpotFleet release managers.

Verifies that:
- compute_fleet_release_decision uses weighted_capacity_to_return (not instance count).
- EC2FleetReleaseManager._sum_weighted_capacity sums WeightedCapacity per InstanceType.
- SpotFleetReleaseManager._sum_weighted_capacity uses per-instance WeightedCapacity from
  describe_spot_fleet_instances, with a fallback to the fleet spec weight-by-type map.
- The release() path passes the weighted sum to modify_fleet / modify_spot_fleet_request.
"""

from typing import Any, cast
from unittest.mock import MagicMock

from orb.providers.aws.infrastructure.handlers.ec2_fleet.release_manager import (
    EC2FleetReleaseManager,
)
from orb.providers.aws.infrastructure.handlers.fleet_release_policy import (
    FleetCapacityInput,
    compute_fleet_release_decision,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.release_manager import (
    SpotFleetReleaseManager,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ec2_release_manager(ec2_client_mock: Any = None) -> EC2FleetReleaseManager:
    aws_client = MagicMock()
    if ec2_client_mock is not None:
        aws_client.ec2_client = ec2_client_mock
    logger = MagicMock()
    aws_ops = MagicMock()
    aws_ops.terminate_instances_with_fallback = MagicMock()

    def identity_retry(fn, operation_type="standard", **kwargs):
        if callable(fn) and not kwargs:
            return fn()
        return fn(**kwargs)

    def identity_paginate(method, key, **kwargs):
        return method(**kwargs).get(key, [])

    def identity_collect(method, key, **kwargs):
        return method(**kwargs).get(key, [])

    return EC2FleetReleaseManager(
        aws_client=aws_client,
        aws_ops=aws_ops,
        request_adapter=None,
        config_port=None,
        logger=logger,
        retry_fn=identity_retry,
        paginate_fn=identity_paginate,
        collect_with_next_token_fn=identity_collect,
        cleanup_on_zero_capacity_fn=MagicMock(),
    )


def _make_spot_release_manager(ec2_client_mock: Any = None) -> SpotFleetReleaseManager:
    aws_client = MagicMock()
    if ec2_client_mock is not None:
        aws_client.ec2_client = ec2_client_mock
    logger = MagicMock()
    aws_ops = MagicMock()
    aws_ops.terminate_instances_with_fallback = MagicMock()

    def identity_retry(fn, operation_type="standard", **kwargs):
        if callable(fn) and not kwargs:
            return fn()
        return fn(**kwargs)

    mgr = SpotFleetReleaseManager(
        aws_client=aws_client,
        aws_ops=aws_ops,
        request_adapter=None,
        cleanup_on_zero_capacity_fn=MagicMock(),
        logger=logger,
        retry_fn=identity_retry,
    )
    return mgr


def _fleet_details(fleet_type: str, total_capacity: int, overrides: list[dict]) -> dict:
    """Build a minimal DescribeFleets entry with LaunchTemplateConfigs overrides."""
    return {
        "Type": fleet_type,
        "TargetCapacitySpecification": {"TotalTargetCapacity": total_capacity},
        "LaunchTemplateConfigs": [{"Overrides": overrides}],
        "Tags": [{"Key": "orb:request-id", "Value": "req-test-001"}],
    }


def _spot_fleet_config(fleet_type: str, target_capacity: int, launch_specs: list[dict]) -> dict:
    """Build a minimal SpotFleetRequestConfig with LaunchSpecifications."""
    return {
        "Type": fleet_type,
        "TargetCapacity": target_capacity,
        "OnDemandTargetCapacity": 0,
        "LaunchSpecifications": launch_specs,
    }


def _active_instances_response(instances: list[dict]) -> dict:
    """Wrap a list of instance dicts into a describe_fleet_instances response."""
    return {"ActiveInstances": instances}


# ---------------------------------------------------------------------------
# fleet_release_policy unit tests
# ---------------------------------------------------------------------------


class TestComputeFleetReleaseDecision:
    def _input(
        self,
        fleet_type: str,
        target: int,
        weighted: int,
        count: int = 1,
    ) -> FleetCapacityInput:
        return FleetCapacityInput(
            fleet_type=fleet_type,
            target_capacity_units=target,
            instances_to_return_count=count,
            instance_weighted_capacity_units=weighted,
        )

    def test_maintain_partial_return_requires_capacity_reduction(self):
        decision = compute_fleet_release_decision(self._input("maintain", 10, 4, count=4))
        assert decision.requires_capacity_reduction is True
        assert decision.has_fleet_record is True
        assert decision.is_full_return is False

    def test_maintain_full_return_is_full(self):
        decision = compute_fleet_release_decision(self._input("maintain", 4, 4))
        assert decision.requires_capacity_reduction is True
        assert decision.is_full_return is True

    def test_maintain_weighted_return_exceeds_capacity_clamps_to_full(self):
        """If weighted sum > current (race / stale data), still produces is_full_return=True."""
        decision = compute_fleet_release_decision(self._input("maintain", 3, 8))
        assert decision.is_full_return is True

    def test_request_fleet_no_capacity_reduction_required(self):
        decision = compute_fleet_release_decision(self._input("request", 5, 2, count=2))
        assert decision.requires_capacity_reduction is False
        assert decision.has_fleet_record is True
        assert decision.is_full_return is False

    def test_instant_fleet_no_fleet_record(self):
        decision = compute_fleet_release_decision(self._input("instant", 2, 2))
        assert decision.requires_capacity_reduction is False
        assert decision.has_fleet_record is False
        assert decision.is_full_return is True

    def test_weighted_capacity_used_not_instance_count(self):
        """Two instances with WeightedCapacity=4 each: subtract 8, not 2."""
        decision = compute_fleet_release_decision(self._input("maintain", 8, 8, count=2))
        assert decision.is_full_return is True

        # With old (instance-count) arithmetic this would be False (8 - 2 == 6 != 0).
        decision_old_style = compute_fleet_release_decision(self._input("maintain", 8, 2, count=2))
        assert decision_old_style.is_full_return is False

    def test_fleet_capacity_input_weighted_equals_target_is_full_return(self):
        """FleetCapacityInput: weighted=4, target=4, count=1 → is_full_return=True."""
        inp = FleetCapacityInput(
            fleet_type="request",
            target_capacity_units=4,
            instances_to_return_count=1,
            instance_weighted_capacity_units=4,
        )
        decision = compute_fleet_release_decision(inp)
        assert decision.is_full_return is True

    def test_fleet_capacity_input_weighted_less_than_target_is_partial(self):
        """FleetCapacityInput: weighted=2, target=4, count=1 → is_full_return=False."""
        inp = FleetCapacityInput(
            fleet_type="request",
            target_capacity_units=4,
            instances_to_return_count=1,
            instance_weighted_capacity_units=2,
        )
        decision = compute_fleet_release_decision(inp)
        assert decision.is_full_return is False


# ---------------------------------------------------------------------------
# EC2FleetReleaseManager._sum_weighted_capacity
# ---------------------------------------------------------------------------


class TestEC2FleetSumWeightedCapacity:
    def _make_manager(self, active_instances: list[dict]) -> EC2FleetReleaseManager:
        ec2 = MagicMock()
        ec2.describe_fleet_instances.return_value = {"ActiveInstances": active_instances}
        return _make_ec2_release_manager(ec2_client_mock=ec2)

    def test_uniform_weights_returns_sum(self):
        """Two m5.large instances each with WeightedCapacity=2 → returns 4."""
        active = [
            {"InstanceId": "i-aaa", "InstanceType": "m5.large"},
            {"InstanceId": "i-bbb", "InstanceType": "m5.large"},
        ]
        overrides = [{"InstanceType": "m5.large", "WeightedCapacity": 2}]
        mgr = self._make_manager(active)
        details = _fleet_details("maintain", 4, overrides)
        result = mgr._sum_weighted_capacity("fleet-111", details, ["i-aaa", "i-bbb"])
        assert result == 4

    def test_mixed_weights_sums_correctly(self):
        """One c5.xlarge (weight 4) + one m5.large (weight 2) → 6."""
        active = [
            {"InstanceId": "i-aaa", "InstanceType": "c5.xlarge"},
            {"InstanceId": "i-bbb", "InstanceType": "m5.large"},
        ]
        overrides = [
            {"InstanceType": "c5.xlarge", "WeightedCapacity": 4},
            {"InstanceType": "m5.large", "WeightedCapacity": 2},
        ]
        mgr = self._make_manager(active)
        details = _fleet_details("maintain", 6, overrides)
        result = mgr._sum_weighted_capacity("fleet-222", details, ["i-aaa", "i-bbb"])
        assert result == 6

    def test_instance_not_in_active_defaults_to_1(self):
        """Instance absent from ActiveInstances (already terminated) defaults to weight 1."""
        active: list[dict] = []  # already gone
        overrides = [{"InstanceType": "c5.xlarge", "WeightedCapacity": 4}]
        mgr = self._make_manager(active)
        details = _fleet_details("maintain", 4, overrides)
        result = mgr._sum_weighted_capacity("fleet-333", details, ["i-gone"])
        assert result == 1

    def test_no_overrides_defaults_to_instance_count(self):
        """Fleet with no WeightedCapacity overrides → sum equals instance count."""
        active = [
            {"InstanceId": "i-aaa", "InstanceType": "t3.medium"},
            {"InstanceId": "i-bbb", "InstanceType": "t3.medium"},
        ]
        mgr = self._make_manager(active)
        details = _fleet_details("maintain", 2, overrides=[])
        result = mgr._sum_weighted_capacity("fleet-444", details, ["i-aaa", "i-bbb"])
        assert result == 2

    def test_describe_fleet_instances_error_defaults_all_to_1(self):
        """If describe_fleet_instances raises, all instances default to weight 1."""
        ec2 = MagicMock()
        ec2.describe_fleet_instances.side_effect = Exception("throttled")
        mgr = _make_ec2_release_manager(ec2_client_mock=ec2)
        overrides = [{"InstanceType": "c5.xlarge", "WeightedCapacity": 4}]
        details = _fleet_details("maintain", 8, overrides)
        result = mgr._sum_weighted_capacity("fleet-555", details, ["i-aaa", "i-bbb"])
        assert result == 2  # 2 instances × default weight 1

    def test_minimum_result_is_1(self):
        """Even with an empty instance_ids list the result is at least 1."""
        ec2 = MagicMock()
        ec2.describe_fleet_instances.return_value = {"ActiveInstances": []}
        mgr = _make_ec2_release_manager(ec2_client_mock=ec2)
        details = _fleet_details("maintain", 4, [])
        # Pass a single instance that defaults to weight 1 (empty active list).
        result = mgr._sum_weighted_capacity("fleet-666", details, ["i-only"])
        assert result >= 1


# ---------------------------------------------------------------------------
# EC2FleetReleaseManager.release() — weighted path end-to-end
# ---------------------------------------------------------------------------


class TestEC2FleetReleaseWeighted:
    def _setup(self, active_instances: list[dict], total_capacity: int, overrides: list[dict]):
        ec2 = MagicMock()
        ec2.describe_fleet_instances.return_value = {"ActiveInstances": active_instances}
        # After our instances are terminated, remaining fleet is also empty → delete
        ec2.describe_fleet_instances.side_effect = [
            {"ActiveInstances": active_instances},  # first call: _sum_weighted_capacity
            {"ActiveInstances": []},  # second call: _fleet_has_no_remaining_instances
        ]
        ec2.modify_fleet.return_value = {}
        ec2.delete_fleets.return_value = {
            "SuccessfulFleetDeletions": [],
            "UnsuccessfulFleetDeletions": [],
        }
        mgr = _make_ec2_release_manager(ec2_client_mock=ec2)
        fleet_details = _fleet_details("maintain", total_capacity, overrides)
        fleet_details["Tags"] = [{"Key": "orb:request-id", "Value": "req-weighted-001"}]
        return mgr, ec2, fleet_details

    def test_modify_fleet_called_with_weighted_new_capacity(self):
        """modify_fleet receives current_capacity - sum(weights), not current_capacity - count."""
        active = [
            {"InstanceId": "i-aaa", "InstanceType": "c5.xlarge"},
            {"InstanceId": "i-bbb", "InstanceType": "c5.xlarge"},
        ]
        # Each c5.xlarge has WeightedCapacity=4; returning 2 → subtract 8.
        overrides = [{"InstanceType": "c5.xlarge", "WeightedCapacity": 4}]
        mgr, ec2, fleet_details = self._setup(active, total_capacity=8, overrides=overrides)

        mgr.release(
            fleet_id="fleet-weighted",
            instance_ids=["i-aaa", "i-bbb"],
            fleet_details=fleet_details,
        )

        ec2.modify_fleet.assert_called_once_with(
            FleetId="fleet-weighted",
            TargetCapacitySpecification={"TotalTargetCapacity": 0},
        )

    def test_maintain_fleet_partial_return_uses_weighted_sum(self):
        """Partial return: 1 of 2 c5.xlarge (weight=4) → new capacity = 8 - 4 = 4."""
        active = [
            {"InstanceId": "i-aaa", "InstanceType": "c5.xlarge"},
            {"InstanceId": "i-bbb", "InstanceType": "c5.xlarge"},
        ]
        overrides = [{"InstanceType": "c5.xlarge", "WeightedCapacity": 4}]
        ec2 = MagicMock()
        # First describe call returns both instances (for _sum_weighted_capacity).
        # Second call returns i-bbb still active (partial return → fleet not deleted).
        ec2.describe_fleet_instances.side_effect = [
            {"ActiveInstances": active},
            {"ActiveInstances": [{"InstanceId": "i-bbb", "InstanceType": "c5.xlarge"}]},
        ]
        ec2.modify_fleet.return_value = {}
        mgr = _make_ec2_release_manager(ec2_client_mock=ec2)
        fleet_details = _fleet_details("maintain", 8, overrides)

        mgr.release(
            fleet_id="fleet-partial",
            instance_ids=["i-aaa"],
            fleet_details=fleet_details,
        )

        ec2.modify_fleet.assert_called_once_with(
            FleetId="fleet-partial",
            TargetCapacitySpecification={"TotalTargetCapacity": 4},
        )

    def test_request_fleet_partial_return_does_not_delete_on_empty_describe_response(self):
        """Regression: request-type EC2Fleet partial return must NOT delete the fleet even
        when describe_fleet_instances transiently returns an empty ActiveInstances list.

        Mirrors the SpotFleet regression: AWS API eventual consistency can briefly show
        no active instances immediately after termination, causing the secondary check to
        fire and incorrectly delete a fleet that still has running instances.
        """
        active = [
            {"InstanceId": "i-aaa", "InstanceType": "t2.small"},
            {"InstanceId": "i-bbb", "InstanceType": "t2.small"},
        ]
        overrides = [{"InstanceType": "t2.small", "WeightedCapacity": 2}]
        ec2 = MagicMock()
        # Simulate AWS API lag: only one describe call (for _sum_weighted_capacity);
        # the secondary _fleet_has_no_remaining_instances call must not happen.
        ec2.describe_fleet_instances.side_effect = [
            {"ActiveInstances": active},  # first call: _sum_weighted_capacity
            {"ActiveInstances": []},  # would-be second call (must not be reached)
        ]
        ec2.modify_fleet.return_value = {}
        ec2.delete_fleets.return_value = {
            "SuccessfulFleetDeletions": [],
            "UnsuccessfulFleetDeletions": [],
        }
        mgr = _make_ec2_release_manager(ec2_client_mock=ec2)
        fleet_details = _fleet_details("request", 4, overrides)
        fleet_details["Tags"] = [{"Key": "orb:request-id", "Value": "req-request-partial"}]

        mgr.release(
            fleet_id="fleet-request-partial",
            instance_ids=["i-aaa"],
            fleet_details=fleet_details,
        )

        # Fleet must NOT be deleted: only a partial return.
        ec2.delete_fleets.assert_not_called()
        # No capacity modification for request fleets.
        ec2.modify_fleet.assert_not_called()
        # The instance must still be terminated.
        assert cast(MagicMock, mgr._aws_ops).terminate_instances_with_fallback.call_count == 1


# ---------------------------------------------------------------------------
# SpotFleetReleaseManager._sum_weighted_capacity
# ---------------------------------------------------------------------------


class TestSpotFleetSumWeightedCapacity:
    def _make_manager(self, active_response: dict) -> SpotFleetReleaseManager:
        ec2 = MagicMock()
        ec2.describe_spot_fleet_instances.return_value = active_response
        return _make_spot_release_manager(ec2_client_mock=ec2)

    def test_per_instance_weight_from_describe(self):
        """Uses WeightedCapacity directly from ActiveInstances response."""
        active_response = {
            "ActiveInstances": [
                {"InstanceId": "i-s1", "InstanceType": "c5.xlarge", "WeightedCapacity": "4"},
                {"InstanceId": "i-s2", "InstanceType": "c5.xlarge", "WeightedCapacity": "4"},
            ]
        }
        fleet_config = _spot_fleet_config("maintain", 8, [])
        mgr = self._make_manager(active_response)
        result = mgr._sum_weighted_capacity("sfr-111", fleet_config, ["i-s1", "i-s2"])
        assert result == 8

    def test_mixed_per_instance_weights(self):
        """Different weights per instance are summed correctly."""
        active_response = {
            "ActiveInstances": [
                {"InstanceId": "i-s1", "InstanceType": "c5.xlarge", "WeightedCapacity": "4"},
                {"InstanceId": "i-s2", "InstanceType": "m5.large", "WeightedCapacity": "2"},
            ]
        }
        fleet_config = _spot_fleet_config("maintain", 6, [])
        mgr = self._make_manager(active_response)
        result = mgr._sum_weighted_capacity("sfr-222", fleet_config, ["i-s1", "i-s2"])
        assert result == 6

    def test_fallback_to_launch_spec_weight_when_instance_missing(self):
        """Instance absent from ActiveInstances: falls back to LaunchSpecifications weight."""
        active_response = {"ActiveInstances": []}
        fleet_config = _spot_fleet_config(
            "maintain", 4, [{"InstanceType": "c5.xlarge", "WeightedCapacity": 4}]
        )
        # The instance is in launch specs but gone from active → use type weight from spec.
        # However since it's not in instance_type_by_id either, it defaults to 1.
        # The per-instance map is empty; type map has c5.xlarge=4 but we can't resolve
        # the instance to a type. Default must be 1.
        mgr = self._make_manager(active_response)
        result = mgr._sum_weighted_capacity("sfr-333", fleet_config, ["i-gone"])
        assert result == 1

    def test_launch_spec_weight_used_when_instance_has_no_weighted_capacity_field(self):
        """Instance in ActiveInstances but WeightedCapacity field absent → type fallback."""
        active_response = {
            "ActiveInstances": [
                {"InstanceId": "i-s1", "InstanceType": "c5.xlarge"},  # no WeightedCapacity
            ]
        }
        fleet_config = _spot_fleet_config(
            "maintain", 4, [{"InstanceType": "c5.xlarge", "WeightedCapacity": 4}]
        )
        mgr = self._make_manager(active_response)
        result = mgr._sum_weighted_capacity("sfr-444", fleet_config, ["i-s1"])
        assert result == 4

    def test_no_weights_anywhere_defaults_to_instance_count(self):
        """No weights in describe or fleet config → sum equals instance count."""
        active_response = {
            "ActiveInstances": [
                {"InstanceId": "i-s1", "InstanceType": "t3.medium"},
                {"InstanceId": "i-s2", "InstanceType": "t3.medium"},
            ]
        }
        fleet_config = _spot_fleet_config("maintain", 2, [])
        mgr = self._make_manager(active_response)
        result = mgr._sum_weighted_capacity("sfr-555", fleet_config, ["i-s1", "i-s2"])
        assert result == 2

    def test_describe_error_defaults_all_to_1(self):
        """If describe_spot_fleet_instances raises, all instances default to weight 1."""
        ec2 = MagicMock()
        ec2.describe_spot_fleet_instances.side_effect = Exception("throttled")
        mgr = _make_spot_release_manager(ec2_client_mock=ec2)
        fleet_config = _spot_fleet_config(
            "maintain", 8, [{"InstanceType": "c5.xlarge", "WeightedCapacity": 4}]
        )
        result = mgr._sum_weighted_capacity("sfr-666", fleet_config, ["i-s1", "i-s2"])
        assert result == 2  # 2 instances × default weight 1


# ---------------------------------------------------------------------------
# SpotFleetReleaseManager.release() — weighted path end-to-end
# ---------------------------------------------------------------------------


class TestSpotFleetReleaseWeighted:
    def _make_fleet_details(self, fleet_type: str, target: int, specs: list[dict]) -> dict:
        return {
            "SpotFleetRequestConfig": {
                "Type": fleet_type,
                "TargetCapacity": target,
                "OnDemandTargetCapacity": 0,
                "LaunchSpecifications": specs,
                "TagSpecifications": [],
            },
            "Tags": [{"Key": "orb:request-id", "Value": "req-spot-weighted"}],
        }

    def test_modify_spot_fleet_called_with_weighted_new_capacity(self):
        """modify_spot_fleet_request receives target - sum(weights), not target - count."""
        active_response = {
            "ActiveInstances": [
                {"InstanceId": "i-s1", "InstanceType": "c5.xlarge", "WeightedCapacity": "4"},
                {"InstanceId": "i-s2", "InstanceType": "c5.xlarge", "WeightedCapacity": "4"},
            ]
        }
        ec2 = MagicMock()
        # First call: _sum_weighted_capacity; second call: _fleet_has_no_remaining_instances
        ec2.describe_spot_fleet_instances.side_effect = [
            active_response,
            {"ActiveInstances": []},
        ]
        ec2.modify_spot_fleet_request.return_value = {}
        ec2.cancel_spot_fleet_requests.return_value = {
            "SuccessfulFleetCancellations": [],
            "UnsuccessfulFleetCancellations": [],
        }
        mgr = _make_spot_release_manager(ec2_client_mock=ec2)
        fleet_details = self._make_fleet_details(
            "maintain", 8, [{"InstanceType": "c5.xlarge", "WeightedCapacity": 4}]
        )

        mgr.release(
            fleet_id="sfr-weighted",
            instance_ids=["i-s1", "i-s2"],
            fleet_details=fleet_details,
        )

        ec2.modify_spot_fleet_request.assert_called_once_with(
            SpotFleetRequestId="sfr-weighted",
            TargetCapacity=0,
            OnDemandTargetCapacity=0,
        )

    def test_partial_spot_release_uses_weighted_sum(self):
        """Partial return: 1 of 2 c5.xlarge (weight=4) → new capacity = 8 - 4 = 4."""
        active_response_sum = {
            "ActiveInstances": [
                {"InstanceId": "i-s1", "InstanceType": "c5.xlarge", "WeightedCapacity": "4"},
                {"InstanceId": "i-s2", "InstanceType": "c5.xlarge", "WeightedCapacity": "4"},
            ]
        }
        active_response_remaining = {
            "ActiveInstances": [
                {"InstanceId": "i-s2", "InstanceType": "c5.xlarge", "WeightedCapacity": "4"},
            ]
        }
        ec2 = MagicMock()
        ec2.describe_spot_fleet_instances.side_effect = [
            active_response_sum,
            active_response_remaining,
        ]
        ec2.modify_spot_fleet_request.return_value = {}
        mgr = _make_spot_release_manager(ec2_client_mock=ec2)
        fleet_details = self._make_fleet_details(
            "maintain", 8, [{"InstanceType": "c5.xlarge", "WeightedCapacity": 4}]
        )

        mgr.release(
            fleet_id="sfr-partial",
            instance_ids=["i-s1"],
            fleet_details=fleet_details,
        )

        ec2.modify_spot_fleet_request.assert_called_once_with(
            SpotFleetRequestId="sfr-partial",
            TargetCapacity=4,
            OnDemandTargetCapacity=0,
        )

    def test_request_fleet_partial_return_does_not_cancel_on_empty_describe_response(self):
        """Regression: request-type SpotFleet partial return must NOT cancel the fleet even
        when describe_spot_fleet_instances transiently returns an empty ActiveInstances list.

        Root cause: AWS API eventual consistency can briefly show no active instances
        immediately after terminate_instances_with_fallback for Mixed50 / weighted scenarios.
        The secondary _fleet_has_no_remaining_instances check must be skipped for request
        fleets because they never auto-refill capacity (unlike maintain fleets).
        """
        # Simulates the Mixed50 scenario: 2 instances of t2.small (weight=2 each) in a
        # request fleet with TargetCapacity=4. We return i-s1; i-s2 should stay running.
        active_response_sum = {
            "ActiveInstances": [
                {"InstanceId": "i-s1", "InstanceType": "t2.small", "WeightedCapacity": "2"},
                {"InstanceId": "i-s2", "InstanceType": "t2.small", "WeightedCapacity": "2"},
            ]
        }
        # Simulate AWS API lag: describe returns empty AFTER the first instance is terminated.
        empty_response = {"ActiveInstances": []}

        ec2 = MagicMock()
        ec2.describe_spot_fleet_instances.side_effect = [
            active_response_sum,  # first call: _sum_weighted_capacity
            empty_response,  # would-be second call: _fleet_has_no_remaining_instances
            # (this call should NOT happen for request fleets)
        ]
        ec2.modify_spot_fleet_request.return_value = {}
        ec2.cancel_spot_fleet_requests.return_value = {
            "SuccessfulFleetCancellations": [],
            "UnsuccessfulFleetCancellations": [],
        }
        mgr = _make_spot_release_manager(ec2_client_mock=ec2)
        fleet_details = self._make_fleet_details(
            "request",
            4,
            [{"InstanceType": "t2.small", "WeightedCapacity": 2}],
        )

        mgr.release(
            fleet_id="sfr-request-partial",
            instance_ids=["i-s1"],
            fleet_details=fleet_details,
        )

        # The fleet must NOT be cancelled: only one of two instances is being returned.
        ec2.cancel_spot_fleet_requests.assert_not_called()
        # No capacity modification for request fleets.
        ec2.modify_spot_fleet_request.assert_not_called()
        # The instance must still be terminated.
        assert cast(MagicMock, mgr._aws_ops).terminate_instances_with_fallback.call_count == 1

    def test_request_fleet_full_return_cancels_when_fleet_empty(self):
        """Request-type fleet: is_full_return=True AND no remaining instances → fleet IS cancelled.

        When all instances are returned (capacity arithmetic says full AND the live
        describe call confirms no instances remain), a request-type fleet MUST be
        cancelled.  AWS does not auto-cancel request fleets, so ORB must do it here.

        The _fleet_has_no_remaining_instances guard prevents stranding running instances
        when arithmetic gives a false-positive full-return (Mixed50 / high-weight case),
        but when the fleet is genuinely empty the guard returns True and cancellation
        proceeds normally.
        """
        active_response = {
            "ActiveInstances": [
                {"InstanceId": "i-s1", "InstanceType": "t2.small", "WeightedCapacity": "2"},
                {"InstanceId": "i-s2", "InstanceType": "t2.small", "WeightedCapacity": "2"},
            ]
        }
        # After returning both instances the fleet is empty.
        empty_response = {"ActiveInstances": []}
        ec2 = MagicMock()
        ec2.describe_spot_fleet_instances.side_effect = [
            active_response,  # first call: _sum_weighted_capacity
            empty_response,  # second call: _fleet_has_no_remaining_instances
        ]
        ec2.modify_spot_fleet_request.return_value = {}
        ec2.cancel_spot_fleet_requests.return_value = {
            "SuccessfulFleetCancellations": [],
            "UnsuccessfulFleetCancellations": [],
        }
        mgr = _make_spot_release_manager(ec2_client_mock=ec2)
        fleet_details = self._make_fleet_details(
            "request",
            4,
            [{"InstanceType": "t2.small", "WeightedCapacity": 2}],
        )

        mgr.release(
            fleet_id="sfr-request-full",
            instance_ids=["i-s1", "i-s2"],
            fleet_details=fleet_details,
        )

        # is_full_return=True from arithmetic AND _fleet_has_no_remaining_instances=True →
        # request fleet IS cancelled.
        ec2.cancel_spot_fleet_requests.assert_called_once_with(
            SpotFleetRequestIds=["sfr-request-full"],
            TerminateInstances=False,
        )
        # No capacity modification for request fleets.
        ec2.modify_spot_fleet_request.assert_not_called()
        # Instances are still terminated.
        assert cast(MagicMock, mgr._aws_ops).terminate_instances_with_fallback.call_count == 1


# ---------------------------------------------------------------------------
# Regression tests: _fleet_has_no_remaining_instances guards request-fleet cancel
# (fixes "cancelled_running" live-test failure)
# ---------------------------------------------------------------------------
#
# Exact scenario that triggered the live failure:
#   Fleet type: SpotFleet request (hostfactory.SpotFleet.Mixed50)
#   TargetCapacity=4
#   Returned: 1 x t3.medium (WeightedCapacity=4) → remaining=0 → is_full_return=True
#   Still running: 2 x t3.micro (WeightedCapacity=1 each) — not returned
#   Old behaviour: cancel_spot_fleet_requests called → fleet enters cancelled_running
#   Correct fix: _fleet_has_no_remaining_instances called; sees i-micro1 and i-micro2
#                still active → returns False → cancellation skipped
# ---------------------------------------------------------------------------


class TestPrimaryCancelPathGating:
    """Regression suite for the _fleet_has_no_remaining_instances guard on request fleets."""

    # -- SpotFleet -------------------------------------------------------

    def _spot_fleet_details(self, fleet_type: str, target: int, specs: list[dict]) -> dict:
        return {
            "SpotFleetRequestConfig": {
                "Type": fleet_type,
                "TargetCapacity": target,
                "OnDemandTargetCapacity": 0,
                "LaunchSpecifications": specs,
                "TagSpecifications": [],
            },
            "Tags": [{"Key": "orb:request-id", "Value": "req-primary-guard"}],
        }

    def test_spot_request_type_weighted_full_return_does_not_cancel(self):
        """SpotFleet request-type: is_full_return=True (weighted arithmetic) → NOT cancelled
        when physical instances are still running.

        Exact Mixed50 scenario: target=4, returning t3.medium weight=4, two t3.micro
        instances (weight=1 each) still running.  Capacity arithmetic yields remaining=0
        making is_full_return=True.  The _fleet_has_no_remaining_instances helper is
        called to verify; it sees i-micro1 and i-micro2 still active (excluded={i-medium}),
        so it returns False and cancellation is skipped.
        """
        # The returning instance has weight=4, exhausting the full target.
        active_response = {
            "ActiveInstances": [
                # t3.medium being returned — weight=4
                {"InstanceId": "i-medium", "InstanceType": "t3.medium", "WeightedCapacity": "4"},
                # Two t3.micros still running — weight=1 each, NOT in instance_ids
                {"InstanceId": "i-micro1", "InstanceType": "t3.micro", "WeightedCapacity": "1"},
                {"InstanceId": "i-micro2", "InstanceType": "t3.micro", "WeightedCapacity": "1"},
            ]
        }
        ec2 = MagicMock()
        # return_value is used for all calls: both _sum_weighted_capacity and
        # _fleet_has_no_remaining_instances see the same three-instance response.
        # After excluding i-medium, i-micro1 and i-micro2 remain → helper returns False.
        ec2.describe_spot_fleet_instances.return_value = active_response
        ec2.modify_spot_fleet_request.return_value = {}
        ec2.cancel_spot_fleet_requests.return_value = {
            "SuccessfulFleetCancellations": [],
            "UnsuccessfulFleetCancellations": [],
        }
        mgr = _make_spot_release_manager(ec2_client_mock=ec2)
        fleet_details = self._spot_fleet_details(
            "request",
            4,
            [
                {"InstanceType": "t3.medium", "WeightedCapacity": 4},
                {"InstanceType": "t3.micro", "WeightedCapacity": 1},
            ],
        )

        mgr.release(
            fleet_id="sfr-mixed50",
            instance_ids=["i-medium"],  # only the t3.medium is returned
            fleet_details=fleet_details,
        )

        # Capacity arithmetic: 4 - 4 = 0 → is_full_return=True.
        # _fleet_has_no_remaining_instances called; sees i-micro1+i-micro2 still up → False.
        # Fleet must NOT be cancelled.
        ec2.cancel_spot_fleet_requests.assert_not_called()
        # No capacity modification for request fleets.
        ec2.modify_spot_fleet_request.assert_not_called()
        # The t3.medium instance is still terminated.
        assert cast(MagicMock, mgr._aws_ops).terminate_instances_with_fallback.call_count == 1

    def test_spot_maintain_type_full_return_cancels_fleet(self):
        """SpotFleet maintain-type: is_full_return=True → fleet IS cancelled (existing behaviour).

        Regression protection: the fix must not break full-return cancellation
        for maintain-type fleets.
        """
        active_response = {
            "ActiveInstances": [
                {"InstanceId": "i-s1", "InstanceType": "c5.xlarge", "WeightedCapacity": "4"},
                {"InstanceId": "i-s2", "InstanceType": "c5.xlarge", "WeightedCapacity": "4"},
            ]
        }
        ec2 = MagicMock()
        # First call: _sum_weighted_capacity; second call: _fleet_has_no_remaining_instances
        ec2.describe_spot_fleet_instances.side_effect = [
            active_response,
            {"ActiveInstances": []},  # fleet empty after termination
        ]
        ec2.modify_spot_fleet_request.return_value = {}
        ec2.cancel_spot_fleet_requests.return_value = {
            "SuccessfulFleetCancellations": [],
            "UnsuccessfulFleetCancellations": [],
        }
        mgr = _make_spot_release_manager(ec2_client_mock=ec2)
        fleet_details = self._spot_fleet_details(
            "maintain",
            8,
            [{"InstanceType": "c5.xlarge", "WeightedCapacity": 4}],
        )

        mgr.release(
            fleet_id="sfr-maintain-full",
            instance_ids=["i-s1", "i-s2"],
            fleet_details=fleet_details,
        )

        # maintain fleet, is_full_return=True, requires_capacity_reduction=True → cancelled.
        ec2.cancel_spot_fleet_requests.assert_called_once_with(
            SpotFleetRequestIds=["sfr-maintain-full"],
            TerminateInstances=False,
        )

    # -- EC2Fleet --------------------------------------------------------

    def _ec2_fleet_details(
        self, fleet_type: str, total_capacity: int, overrides: list[dict]
    ) -> dict:
        return {
            "Type": fleet_type,
            "TargetCapacitySpecification": {"TotalTargetCapacity": total_capacity},
            "LaunchTemplateConfigs": [{"Overrides": overrides}],
            "Tags": [{"Key": "orb:request-id", "Value": "req-ec2-primary-guard"}],
        }

    def test_ec2_request_type_weighted_full_return_does_not_delete(self):
        """EC2Fleet request-type: is_full_return=True (weighted arithmetic) → NOT deleted
        when physical instances are still running.

        Mirrors the SpotFleet Mixed50 scenario for EC2Fleet: target=4, returning a
        t3.medium (weight=4) while two t3.micro instances (weight=1 each) still run.
        The _fleet_has_no_remaining_instances helper sees i-micro1 and i-micro2 still
        active (excluded={i-medium}), so it returns False and deletion is skipped.
        """
        active_instances = [
            {"InstanceId": "i-medium", "InstanceType": "t3.medium"},
            {"InstanceId": "i-micro1", "InstanceType": "t3.micro"},
            {"InstanceId": "i-micro2", "InstanceType": "t3.micro"},
        ]
        ec2 = MagicMock()
        # return_value used for all describe calls; after excluding i-medium,
        # i-micro1 and i-micro2 remain → _fleet_has_no_remaining_instances returns False.
        ec2.describe_fleet_instances.return_value = {"ActiveInstances": active_instances}
        ec2.modify_fleet.return_value = {}
        ec2.delete_fleets.return_value = {
            "SuccessfulFleetDeletions": [],
            "UnsuccessfulFleetDeletions": [],
        }
        mgr = _make_ec2_release_manager(ec2_client_mock=ec2)
        fleet_details = self._ec2_fleet_details(
            "request",
            4,
            [
                {"InstanceType": "t3.medium", "WeightedCapacity": 4},
                {"InstanceType": "t3.micro", "WeightedCapacity": 1},
            ],
        )

        mgr.release(
            fleet_id="fleet-ec2-mixed50",
            instance_ids=["i-medium"],  # only the t3.medium is returned
            fleet_details=fleet_details,
        )

        # Capacity arithmetic: 4 - 4 = 0 → is_full_return=True.
        # _fleet_has_no_remaining_instances sees i-micro1+i-micro2 still up → False.
        # Fleet must NOT be deleted.
        ec2.delete_fleets.assert_not_called()
        # No capacity modification for request fleets.
        ec2.modify_fleet.assert_not_called()
        # The t3.medium instance is still terminated.
        assert cast(MagicMock, mgr._aws_ops).terminate_instances_with_fallback.call_count == 1

    def test_ec2_maintain_type_full_return_deletes_fleet(self):
        """EC2Fleet maintain-type: is_full_return=True → fleet IS deleted (existing behaviour).

        Regression protection: the fix must not break full-return deletion for
        maintain-type EC2 fleets.
        """
        active_instances = [
            {"InstanceId": "i-s1", "InstanceType": "c5.xlarge"},
            {"InstanceId": "i-s2", "InstanceType": "c5.xlarge"},
        ]
        ec2 = MagicMock()
        # First call: _sum_weighted_capacity; second call: _fleet_has_no_remaining_instances
        ec2.describe_fleet_instances.side_effect = [
            {"ActiveInstances": active_instances},
            {"ActiveInstances": []},  # fleet empty after termination
        ]
        ec2.modify_fleet.return_value = {}
        ec2.delete_fleets.return_value = {
            "SuccessfulFleetDeletions": [],
            "UnsuccessfulFleetDeletions": [],
        }
        mgr = _make_ec2_release_manager(ec2_client_mock=ec2)
        fleet_details = self._ec2_fleet_details(
            "maintain",
            8,
            [{"InstanceType": "c5.xlarge", "WeightedCapacity": 4}],
        )

        mgr.release(
            fleet_id="fleet-ec2-maintain-full",
            instance_ids=["i-s1", "i-s2"],
            fleet_details=fleet_details,
        )

        # maintain fleet, is_full_return=True, requires_capacity_reduction=True → deleted.
        ec2.delete_fleets.assert_called_once_with(
            FleetIds=["fleet-ec2-maintain-full"],
            TerminateInstances=True,
        )

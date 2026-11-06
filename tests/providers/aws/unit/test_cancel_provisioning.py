"""Provider-side cancel: outcome mapping, retries, guards for already-removed resources.

All AWS calls are mocked; no real AWS connections are made.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from orb.domain.base.operations import Operation, OperationType
from orb.providers.aws.infrastructure.adapters.aws_provisioning_adapter import (
    AWSProvisioningAdapter,
)
from orb.providers.aws.infrastructure.handlers.asg.capacity_manager import ASGCapacityManager
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.release_manager import (
    EC2FleetReleaseManager,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.release_manager import (
    SpotFleetReleaseManager,
)
from orb.providers.aws.services.capability_service import AWSCapabilityService
from orb.providers.aws.services.instance_operation_service import AWSInstanceOperationService

pytestmark = pytest.mark.unit

SLEEP = "orb.infrastructure.resilience.retry_decorator.time.sleep"


def _error(code: str, operation: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": f"{code} text"}}, operation)


def _real_retry_handler(handler_cls):
    aws_client = MagicMock()
    aws_client.get_selected_aws_provider_config.return_value = None
    config_port = MagicMock()
    config_port.app_config = None
    config_port.get_resource_prefix.return_value = ""
    return handler_cls(aws_client, MagicMock(), MagicMock(), MagicMock(), config_port=config_port)


def _adapter_for(handlers: dict[str, Any]) -> AWSProvisioningAdapter:
    strategy = MagicMock()
    strategy.get_handler.side_effect = lambda api: handlers[api]
    return AWSProvisioningAdapter(MagicMock(), MagicMock(), strategy)


class TestOutcomeMapping:
    def test_success_is_aborted_and_already_gone_is_reported(self):
        handler = MagicMock()
        handler.cancel_resource_with_members.side_effect = [
            {"status": "success", "message": "m"},
            {"status": "success", "already_gone": True, "message": "m"},
        ]
        outcomes = _adapter_for({"EC2Fleet": handler}).cancel_resources(
            "EC2Fleet", ["f-1", "f-2"], "req-1"
        )
        assert [o["outcome"] for o in outcomes] == ["aborted", "already_gone"]

    def test_handler_error_dict_becomes_failed(self):
        handler = MagicMock()
        handler.cancel_resource_with_members.return_value = {"status": "error", "message": "denied"}
        (outcome,) = _adapter_for({"ASG": handler}).cancel_resources("ASG", ["a-1"], "req-1")
        assert outcome == {"resource_id": "a-1", "outcome": "failed", "message": "denied"}

    def test_handler_exception_becomes_failed(self):
        handler = MagicMock()
        handler.cancel_resource_with_members.side_effect = RuntimeError("kaboom")
        (outcome,) = _adapter_for({"ASG": handler}).cancel_resources("ASG", ["a-1"], "req-1")
        assert outcome["outcome"] == "failed"
        assert "kaboom" in outcome["message"]

    def test_a_failed_resource_does_not_stop_the_others_and_a_retry_finishes_the_rest(self):
        handler = MagicMock()
        handler.cancel_resource_with_members.side_effect = [
            {"status": "success", "message": ""},
            {"status": "error", "message": "throttled"},
        ]
        adapter = _adapter_for({"EC2Fleet": handler})
        first = adapter.cancel_resources("EC2Fleet", ["f-a", "f-b"], "req-1")
        assert [o["outcome"] for o in first] == ["aborted", "failed"]
        assert handler.cancel_resource_with_members.call_count == 2

        handler.cancel_resource_with_members.side_effect = [
            {"status": "success", "already_gone": True, "message": ""},
            {"status": "success", "message": ""},
        ]
        second = adapter.cancel_resources("EC2Fleet", ["f-a", "f-b"], "req-1")
        assert [o["outcome"] for o in second] == ["already_gone", "aborted"]


class TestOperationResult:
    @staticmethod
    def _service(outcomes):
        adapter = MagicMock()
        adapter.cancel_resources.return_value = outcomes
        service = AWSInstanceOperationService(
            aws_client=MagicMock(),
            logger=MagicMock(),
            provisioning_adapter=adapter,
            machine_adapter=MagicMock(),
        )
        service._list_live_members = MagicMock(return_value=["i-1"])  # type: ignore[method-assign]
        return service

    @staticmethod
    def _operation(**overrides):
        parameters = {"request_id": "req-1", "provider_api": "EC2Fleet", "resource_ids": ["f-1"]}
        parameters.update(overrides)
        return Operation(OperationType.CANCEL_PROVISIONING, parameters)

    @pytest.mark.asyncio
    async def test_failed_resource_makes_the_operation_fail_and_skips_the_listing(self):
        service = self._service([{"resource_id": "f-1", "outcome": "failed", "message": "no"}])
        result = await service.cancel_provisioning(self._operation())
        assert result.success is False
        assert "f-1" in (result.error_message or "")
        assert result.data["resources"][0]["outcome"] == "failed"
        service._list_live_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_listing_failure_after_a_successful_abort_fails_the_operation(self):
        service = self._service([{"resource_id": "f-1", "outcome": "aborted", "message": ""}])
        service._list_live_members.side_effect = RuntimeError("describe down")
        result = await service.cancel_provisioning(self._operation())
        assert result.success is False
        assert result.error_code == "CANCEL_PROVISIONING_LISTING_FAILED"

    @pytest.mark.asyncio
    async def test_success_carries_members_per_resource(self):
        service = self._service([{"resource_id": "f-1", "outcome": "aborted", "message": ""}])
        result = await service.cancel_provisioning(self._operation())
        assert result.success is True
        assert result.data["resources"][0]["members"] == ["i-1"]

    @pytest.mark.asyncio
    async def test_missing_parameters_are_rejected(self):
        service = self._service([])
        result = await service.cancel_provisioning(self._operation(resource_ids=[]))
        assert result.success is False
        assert result.error_code == "MISSING_CANCEL_PARAMETERS"

    @pytest.mark.asyncio
    async def test_unknown_provider_api_fails_instead_of_raising(self):
        service = self._service([])
        service._provisioning_adapter.cancel_resources.side_effect = ValueError("no handler")
        result = await service.cancel_provisioning(self._operation())
        assert result.success is False


class TestCapabilityAndDispatch:
    def test_aws_advertises_the_operation(self):
        capabilities = AWSCapabilityService(MagicMock(), MagicMock()).get_capabilities()
        assert capabilities.supports_operation(OperationType.CANCEL_PROVISIONING)

    @pytest.mark.asyncio
    async def test_strategy_routes_the_operation_to_the_instance_service(self):
        from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        strategy = object.__new__(AWSProviderStrategy)
        service = MagicMock()
        service.cancel_provisioning = AsyncMock(return_value="routed")
        strategy._get_instance_service = lambda: service  # type: ignore[method-assign]
        operation = Operation(OperationType.CANCEL_PROVISIONING, {"resource_ids": ["f-1"]})

        assert await strategy._execute_operation_internal(operation) == "routed"
        service.cancel_provisioning.assert_awaited_once_with(operation)


@pytest.fixture(autouse=True)
def _plain_pagination():
    """Mocked clients cannot paginate; answer every paginated describe with one page."""
    with patch(
        "orb.providers.aws.infrastructure.utils.paginate",
        side_effect=lambda method, key, **kw: method(**kw).get(key, []),
    ):
        yield


class TestFaultInjectedAborts:
    def test_throttled_fleet_delete_is_retried_to_success(self):
        handler = _real_retry_handler(EC2FleetHandler)
        ec2 = handler.aws_client.ec2_client
        ec2.describe_fleets.return_value = {"Fleets": [{"FleetId": "f-1", "FleetState": "active"}]}
        ec2.describe_fleet_instances.return_value = {"ActiveInstances": []}
        ec2.delete_fleets.side_effect = [
            _error("RequestLimitExceeded"),
            {"SuccessfulFleetDeletions": [{"FleetId": "f-1"}], "UnsuccessfulFleetDeletions": []},
        ]
        with patch(SLEEP):
            result = handler.cancel_resource("f-1", "req-1")
        assert result["status"] == "success"
        assert result["already_gone"] is False
        assert ec2.delete_fleets.call_count == 2
        assert ec2.delete_fleets.call_args.kwargs["TerminateInstances"] is True

    def test_unsuccessful_deletion_entry_is_a_failure(self):
        handler = _real_retry_handler(EC2FleetHandler)
        ec2 = handler.aws_client.ec2_client
        ec2.describe_fleets.return_value = {"Fleets": [{"FleetId": "f-1", "FleetState": "active"}]}
        ec2.describe_fleet_instances.return_value = {"ActiveInstances": []}
        ec2.delete_fleets.return_value = {
            "SuccessfulFleetDeletions": [],
            "UnsuccessfulFleetDeletions": [
                {"FleetId": "f-1", "Error": {"Code": "unexpectedError", "Message": "x"}}
            ],
        }
        assert handler.cancel_resource("f-1", "req-1")["status"] == "error"

    @pytest.mark.parametrize("confirmed_deleted, expected", [(True, "success"), (False, "error")])
    def test_not_in_deletable_state_counts_only_when_describe_confirms(
        self, confirmed_deleted, expected
    ):
        handler = _real_retry_handler(EC2FleetHandler)
        ec2 = handler.aws_client.ec2_client
        states = [
            {"FleetId": "f-1", "FleetState": "active"},
            {"FleetId": "f-1", "FleetState": "deleted" if confirmed_deleted else "active"},
        ]
        ec2.describe_fleets.side_effect = [
            {"Fleets": [states[0]]},
            {"Fleets": [states[1]]},
        ]
        ec2.describe_fleet_instances.return_value = {"ActiveInstances": []}
        ec2.delete_fleets.return_value = {
            "SuccessfulFleetDeletions": [],
            "UnsuccessfulFleetDeletions": [
                {"FleetId": "f-1", "Error": {"Code": "fleetNotInDeletableState", "Message": ""}}
            ],
        }
        assert handler.cancel_resource("f-1", "req-1")["status"] == expected

    def test_spot_fleet_throttle_then_success_and_already_cancelled_fleet_is_a_noop(self):
        handler = _real_retry_handler(SpotFleetHandler)
        ec2 = handler.aws_client.ec2_client
        ec2.describe_spot_fleet_requests.return_value = {
            "SpotFleetRequestConfigs": [
                {"SpotFleetRequestId": "sfr-1", "SpotFleetRequestState": "active"}
            ]
        }
        ec2.describe_spot_fleet_instances.return_value = {"ActiveInstances": []}
        ec2.cancel_spot_fleet_requests.side_effect = [
            _error("RequestLimitExceeded"),
            {"SuccessfulFleetRequests": [{"SpotFleetRequestId": "sfr-1"}]},
        ]
        with patch(SLEEP):
            first = handler.cancel_resource("sfr-1", "req-1")
        assert first["status"] == "success" and first["already_gone"] is False

        ec2.cancel_spot_fleet_requests.reset_mock()
        ec2.describe_spot_fleet_requests.return_value = {
            "SpotFleetRequestConfigs": [
                {"SpotFleetRequestId": "sfr-1", "SpotFleetRequestState": "cancelled_terminating"}
            ]
        }
        again = handler.cancel_resource("sfr-1", "req-1")
        assert again["status"] == "success" and again["already_gone"] is True
        ec2.cancel_spot_fleet_requests.assert_not_called()

    def test_termination_protection_error_is_a_failure_not_a_retry(self):
        handler = _real_retry_handler(EC2FleetHandler)
        ec2 = handler.aws_client.ec2_client
        ec2.describe_fleets.return_value = {"Fleets": [{"FleetId": "f-1", "FleetState": "active"}]}
        ec2.describe_fleet_instances.return_value = {"ActiveInstances": []}
        ec2.delete_fleets.side_effect = _error("OperationNotPermitted")
        with patch(SLEEP) as sleep:
            result = handler.cancel_resource("f-1", "req-1")
        assert result["status"] == "error"
        assert "OperationNotPermitted" in result["message"]
        assert ec2.delete_fleets.call_count == 1
        assert sleep.call_count == 0

    def test_asg_delete_in_progress_conflict_is_retried(self):
        handler = _real_retry_handler(ASGHandler)
        asg = handler.aws_client.autoscaling_client
        asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"AutoScalingGroupName": "g-1", "Instances": []}]
        }
        asg.delete_auto_scaling_group.side_effect = [_error("ScalingActivityInProgress"), {}]
        with patch(SLEEP):
            result = handler.cancel_resource("g-1", "req-1")
        assert result["status"] == "success"
        assert asg.delete_auto_scaling_group.call_count == 2

    def test_asg_is_scaled_to_zero_before_the_force_delete(self):
        handler = _real_retry_handler(ASGHandler)
        asg = handler.aws_client.autoscaling_client
        order: list[str] = []
        asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"AutoScalingGroupName": "g-1", "Instances": []}]
        }
        asg.update_auto_scaling_group.side_effect = lambda **kw: order.append(f"zero:{kw}") or {}
        asg.delete_auto_scaling_group.side_effect = lambda **kw: order.append("delete") or {}

        assert handler.cancel_resource("g-1", "req-1")["status"] == "success"

        assert order[0] == "zero:" + str(
            {"AutoScalingGroupName": "g-1", "MinSize": 0, "DesiredCapacity": 0}
        )
        assert order[1] == "delete"

    def test_asg_zero_step_failure_is_tolerated(self):
        handler = _real_retry_handler(ASGHandler)
        asg = handler.aws_client.autoscaling_client
        asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"AutoScalingGroupName": "g-1", "Instances": []}]
        }
        asg.update_auto_scaling_group.side_effect = _error("ValidationError")
        assert handler.cancel_resource("g-1", "req-1")["status"] == "success"
        asg.delete_auto_scaling_group.assert_called_once()

    def test_deleting_asg_is_already_gone_and_left_alone(self):
        handler = _real_retry_handler(ASGHandler)
        asg = handler.aws_client.autoscaling_client
        asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {"AutoScalingGroupName": "g-1", "Status": "Delete in progress", "Instances": []}
            ]
        }

        result = handler.cancel_resource("g-1", "req-1")

        assert result["status"] == "success" and result["already_gone"] is True
        asg.delete_auto_scaling_group.assert_not_called()
        asg.update_auto_scaling_group.assert_not_called()

    def test_absent_asg_is_confirmed_gone_by_the_delete_without_scaling(self):
        handler = _real_retry_handler(ASGHandler)
        asg = handler.aws_client.autoscaling_client
        asg.describe_auto_scaling_groups.return_value = {"AutoScalingGroups": []}
        asg.delete_auto_scaling_group.side_effect = _error("ValidationError")

        result = handler.cancel_resource("g-1", "req-1")

        assert result["status"] == "success" and result["already_gone"] is True
        asg.update_auto_scaling_group.assert_not_called()
        asg.delete_auto_scaling_group.assert_called_once()

    def test_asg_delete_failure_with_unreadable_group_is_a_failure(self):
        handler = _real_retry_handler(ASGHandler)
        asg = handler.aws_client.autoscaling_client
        asg.describe_auto_scaling_groups.side_effect = _error("ValidationError")
        asg.delete_auto_scaling_group.side_effect = _error("AccessDenied")
        with patch(SLEEP):
            assert handler.cancel_resource("g-1", "req-1")["status"] == "error"


def _direct_retry(fn, *args, operation_type="standard", operation_name=None, **kwargs):
    return fn(*args, **kwargs)


class TestReturnAfterAbortDoesNotTouchTheDeletedResource:
    def _ec2_manager(self):
        aws_client = MagicMock()
        aws_ops = MagicMock()
        manager = EC2FleetReleaseManager(
            aws_client=aws_client,
            aws_ops=aws_ops,
            request_adapter=None,
            config_port=None,
            logger=MagicMock(),
            retry_fn=_direct_retry,
            paginate_fn=lambda method, key, **kw: method(**kw).get(key, []),
            collect_with_next_token_fn=lambda method, key, **kw: method(**kw).get(key, []),
            cleanup_on_zero_capacity_fn=MagicMock(),
        )
        return manager, aws_client.ec2_client, aws_ops

    @pytest.mark.parametrize("state", ["deleted", "deleted_running", "deleted_terminating"])
    def test_ec2_fleet_in_a_deleted_state_is_not_modified_or_deleted(self, state):
        manager, ec2, aws_ops = self._ec2_manager()
        details = {
            "Type": "maintain",
            "FleetState": state,
            "TargetCapacitySpecification": {"TotalTargetCapacity": 3},
            "Tags": [{"Key": "orb:request-id", "Value": "req-1"}],
        }

        already_gone = manager.release("f-1", ["i-1"], details)

        assert already_gone is True
        ec2.modify_fleet.assert_not_called()
        ec2.delete_fleets.assert_not_called()
        aws_ops.terminate_instances_with_fallback.assert_called_once()
        assert aws_ops.terminate_instances_with_fallback.call_args.args[0] == ["i-1"]
        manager._cleanup_on_zero_capacity.assert_called_once()

    def test_ec2_fleet_not_found_fast_path_accepts_an_empty_instance_list(self):
        manager, ec2, aws_ops = self._ec2_manager()
        ec2.describe_fleets.return_value = {"Fleets": []}
        aws_ops.terminate_instances_with_fallback = MagicMock(
            return_value={"terminated_instances": []}
        )

        already_gone = manager.release("f-1", [], {}, request_id="req-1")

        assert already_gone is True
        ec2.modify_fleet.assert_not_called()
        ec2.delete_fleets.assert_not_called()
        assert aws_ops.terminate_instances_with_fallback.call_args.args[0] == []

    def test_a_live_fleet_is_still_released_normally(self):
        manager, ec2, aws_ops = self._ec2_manager()
        details = {
            "Type": "request",
            "FleetState": "active",
            "TargetCapacitySpecification": {"TotalTargetCapacity": 1},
        }
        ec2.describe_fleet_instances.return_value = {"ActiveInstances": []}
        ec2.delete_fleets.return_value = {"UnsuccessfulFleetDeletions": []}

        assert manager.release("f-1", ["i-1"], details) is False
        ec2.delete_fleets.assert_called_once()

    @pytest.mark.parametrize("state", ["cancelled", "cancelled_running", "cancelled_terminating"])
    def test_spot_fleet_in_a_cancelled_state_is_not_modified_or_cancelled_again(self, state):
        aws_client = MagicMock()
        aws_ops = MagicMock()
        manager = SpotFleetReleaseManager(
            aws_client=aws_client,
            aws_ops=aws_ops,
            request_adapter=None,
            cleanup_on_zero_capacity_fn=MagicMock(),
            logger=MagicMock(),
            retry_fn=_direct_retry,
        )
        ec2 = aws_client.ec2_client
        details = {
            "SpotFleetRequestState": state,
            "SpotFleetRequestConfig": {
                "Type": "maintain",
                "TargetCapacity": 3,
                "TagSpecifications": [
                    {"Tags": [{"Key": "orb:request-id", "Value": "req-1"}]},
                ],
            },
        }

        assert manager.release("sfr-1", ["i-1"], details) is True

        ec2.modify_spot_fleet_request.assert_not_called()
        ec2.cancel_spot_fleet_requests.assert_not_called()
        aws_ops.terminate_instances_with_fallback.assert_called_once()


class TestAsgReturnAfterAbort:
    def _manager(self):
        aws_client = MagicMock()
        aws_ops = MagicMock()
        cleanup = MagicMock()
        manager = ASGCapacityManager(
            aws_client=aws_client,
            aws_ops=aws_ops,
            request_adapter=None,
            cleanup_on_zero_capacity_fn=cleanup,
            logger=MagicMock(),
            retry_with_backoff=_direct_retry,
            chunk_list=lambda lst, n: [lst],
        )
        return manager, aws_client.autoscaling_client, aws_ops, cleanup

    def test_group_being_deleted_is_not_detached_from_and_instances_are_terminated_directly(self):
        manager, asg, aws_ops, cleanup = self._manager()
        details = {
            "AutoScalingGroupName": "g-1",
            "DesiredCapacity": 2,
            "Status": "Delete in progress",
        }

        manager.release_instances("g-1", ["i-1"], details)

        asg.terminate_instance_in_auto_scaling_group.assert_not_called()
        asg.update_auto_scaling_group.assert_not_called()
        asg.delete_auto_scaling_group.assert_not_called()
        aws_ops.terminate_instances_with_fallback.assert_called_once()
        assert aws_ops.terminate_instances_with_fallback.call_args.args[0] == ["i-1"]
        cleanup.assert_called_once_with("asg", "g-1", ["i-1"])

    def test_absent_group_terminates_directly_and_tidies_the_template(self):
        manager, asg, aws_ops, cleanup = self._manager()
        asg.describe_auto_scaling_groups.return_value = {"AutoScalingGroups": []}
        asg.delete_auto_scaling_group.side_effect = _error("ValidationError")

        manager.release_instances("g-1", ["i-1"], {})

        asg.terminate_instance_in_auto_scaling_group.assert_not_called()
        aws_ops.terminate_instances_with_fallback.assert_called_once()
        cleanup.assert_called_once()

    def test_resource_mapping_path_carries_the_group_status_into_the_details(self):
        handler = _real_retry_handler(ASGHandler)
        asg = handler.aws_client.autoscaling_client
        asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupName": "g-1",
                    "DesiredCapacity": 2,
                    "MinSize": 0,
                    "MaxSize": 3,
                    "Status": "Delete in progress",
                }
            ]
        }
        handler._capacity_manager.release_instances = MagicMock()  # type: ignore[method-assign]

        with patch(SLEEP):
            handler.release_hosts(["i-1"], resource_mapping={"i-1": ("g-1", 2)}, request_id="r")

        details = handler._capacity_manager.release_instances.call_args.args[2]
        assert details["Status"] == "Delete in progress"

    def test_lookup_path_classifies_instances_of_a_deleted_group_as_standalone(self):
        handler = _real_retry_handler(ASGHandler)
        asg = handler.aws_client.autoscaling_client
        # The group is gone, so the instances no longer map to any group.
        asg.describe_auto_scaling_instances.return_value = {"AutoScalingInstances": []}
        handler.aws_ops.terminate_instances_with_fallback = MagicMock()
        handler._capacity_manager.release_instances = MagicMock()  # type: ignore[method-assign]

        with patch(SLEEP):
            handler.release_hosts(["i-1"])

        handler._capacity_manager.release_instances.assert_not_called()
        handler.aws_ops.terminate_instances_with_fallback.assert_called_once()


class TestFleetDeletedWithInstancesStillRunning:
    """deleted_running / cancelled_running fleets keep billing until their instances end."""

    def _ec2(self):
        aws_client = MagicMock()
        aws_ops = MagicMock()
        manager = EC2FleetReleaseManager(
            aws_client=aws_client,
            aws_ops=aws_ops,
            request_adapter=None,
            config_port=None,
            logger=MagicMock(),
            retry_fn=_direct_retry,
            paginate_fn=lambda method, key, **kw: method(**kw).get(key, []),
            collect_with_next_token_fn=lambda method, key, **kw: method(**kw).get(key, []),
            cleanup_on_zero_capacity_fn=MagicMock(),
        )
        return manager, aws_client.ec2_client, aws_ops

    def _spot(self):
        aws_client = MagicMock()
        aws_ops = MagicMock()
        manager = SpotFleetReleaseManager(
            aws_client=aws_client,
            aws_ops=aws_ops,
            request_adapter=None,
            cleanup_on_zero_capacity_fn=MagicMock(),
            logger=MagicMock(),
            retry_fn=_direct_retry,
        )
        return manager, aws_client.ec2_client, aws_ops

    def test_ec2_fleet_deleted_running_has_its_members_terminated(self):
        manager, ec2, aws_ops = self._ec2()
        ec2.describe_fleet_instances.return_value = {
            "ActiveInstances": [{"InstanceId": "i-1"}, {"InstanceId": "i-2"}]
        }
        ec2.delete_fleets.return_value = {"UnsuccessfulFleetDeletions": []}
        details = {"Type": "maintain", "FleetState": "deleted_running"}

        assert manager.release("f-1", [], details, "req-1") is True

        assert ec2.delete_fleets.call_args.kwargs["TerminateInstances"] is True
        aws_ops.terminate_instances_with_fallback.assert_called_once()
        assert aws_ops.terminate_instances_with_fallback.call_args.args[0] == ["i-1", "i-2"]

    def test_spot_fleet_cancelled_running_has_its_members_terminated(self):
        manager, ec2, aws_ops = self._spot()
        ec2.describe_spot_fleet_instances.return_value = {
            "ActiveInstances": [{"InstanceId": "i-1"}]
        }
        ec2.cancel_spot_fleet_requests.return_value = {"UnsuccessfulFleetRequests": []}
        details = {"SpotFleetRequestState": "cancelled_running", "SpotFleetRequestConfig": {}}

        assert manager.release("sfr-1", [], details, "req-1") is True

        assert ec2.cancel_spot_fleet_requests.call_args.kwargs["TerminateInstances"] is True
        assert aws_ops.terminate_instances_with_fallback.call_args.args[0] == ["i-1"]

    def test_members_that_cannot_be_listed_fail_loudly(self):
        manager, ec2, aws_ops = self._ec2()
        ec2.describe_fleet_instances.side_effect = RuntimeError("describe down")
        ec2.delete_fleets.return_value = {"UnsuccessfulFleetDeletions": []}

        with pytest.raises(Exception, match="may still be running"):
            manager.release("f-1", [], {"Type": "maintain", "FleetState": "deleted_running"})
        aws_ops.terminate_instances_with_fallback.assert_not_called()

    @pytest.mark.parametrize("state", ["deleted_terminating", "deleted"])
    def test_a_terminating_fleet_is_left_alone(self, state):
        manager, ec2, aws_ops = self._ec2()
        ec2.describe_fleet_instances.return_value = {"ActiveInstances": []}

        manager.release("f-1", [], {"Type": "maintain", "FleetState": state})

        ec2.delete_fleets.assert_not_called()
        aws_ops.terminate_instances_with_fallback.assert_not_called()

    def test_a_spot_maintain_fleet_is_cancelled_with_termination_after_a_full_return(self):
        manager, ec2, _ = self._spot()
        ec2.cancel_spot_fleet_requests.return_value = {"UnsuccessfulFleetRequests": []}

        manager._cancel_or_delete_fleet("sfr-1", terminate_instances=False, is_maintain=True)
        assert ec2.cancel_spot_fleet_requests.call_args.kwargs["TerminateInstances"] is True

        manager._cancel_or_delete_fleet("sfr-1", terminate_instances=False, is_maintain=False)
        assert ec2.cancel_spot_fleet_requests.call_args.kwargs["TerminateInstances"] is False


class TestAsgMembersAreTerminatedByTheAbort:
    @staticmethod
    def _handler(members):
        handler = _real_retry_handler(ASGHandler)
        asg = handler.aws_client.autoscaling_client
        asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {"AutoScalingGroupName": "g-1", "Instances": [{"InstanceId": i} for i in members]}
            ]
        }
        handler.aws_ops.terminate_instances_with_fallback = MagicMock()
        return handler

    def test_listed_members_are_terminated_after_the_delete(self):
        handler = self._handler(["i-1", "i-2"])

        assert handler.cancel_resource("g-1", "req-1")["status"] == "success"

        handler.aws_ops.terminate_instances_with_fallback.assert_called_once()
        assert handler.aws_ops.terminate_instances_with_fallback.call_args.args[0] == ["i-1", "i-2"]

    def test_a_group_without_members_terminates_nothing(self):
        handler = self._handler([])
        handler.cancel_resource("g-1", "req-1")
        handler.aws_ops.terminate_instances_with_fallback.assert_not_called()

    def test_members_that_cannot_be_terminated_fail_the_cancel(self):
        handler = self._handler(["i-1"])
        handler.aws_ops.terminate_instances_with_fallback.side_effect = RuntimeError("denied")

        result = handler.cancel_resource("g-1", "req-1")

        assert result["status"] == "error"
        assert "may still be running" in result["message"]


class TestCleanupReadsTheConfigPortWhenUsed:
    def test_a_port_injected_after_the_handler_was_built_is_used(self):
        handler = _real_retry_handler(ASGHandler)
        handler.config_port = None
        handler._lt_cleanup._config_port_provider = lambda: handler.config_port
        calls = []
        handler._lt_cleanup.delete_orb_launch_template = lambda *a: calls.append(a)  # type: ignore[method-assign]

        handler._cleanup_on_zero_capacity("asg", "req-1")
        assert calls == []  # no port yet: cleanup stays off

        handler.config_port = MagicMock()
        handler._cleanup_on_zero_capacity("asg", "req-1")
        assert len(calls) == 1

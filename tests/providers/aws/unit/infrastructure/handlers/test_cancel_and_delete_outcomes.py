"""Cancel and delete paths must never report success while resources may survive.

All AWS calls are mocked; no real AWS connections are made.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from orb.infrastructure.resilience.exceptions import MaxRetriesExceededError
from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from orb.providers.aws.infrastructure.handlers.asg.capacity_manager import ASGCapacityManager
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.handlers.shared.delete_outcome import (
    check_unsuccessful_fleet_response,
    delete_asg_or_confirm_gone,
    emit_alert,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.release_manager import (
    SpotFleetReleaseManager,
)


def _client_error(code: str, op: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "m"}}, op)


def _direct_retry(fn, *args, operation_type="standard", **kwargs):
    return fn(*args, **kwargs)


def _events(logger: MagicMock) -> list[str]:
    return [
        c.kwargs["extra"]["event"]
        for c in logger.error.call_args_list
        if "extra" in c.kwargs and "event" in c.kwargs["extra"]
    ]


def _run_instances_handler() -> Any:
    handler: Any = RunInstancesHandler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    handler._retry_with_backoff = _direct_retry
    handler._cleanup_on_zero_capacity = MagicMock()
    return handler


def _reservation(*instances: tuple[str, str]) -> dict[str, Any]:
    return {
        "Reservations": [
            {"Instances": [{"InstanceId": i, "State": {"Name": s}} for i, s in instances]}
        ]
    }


# ---------------------------------------------------------------------------
# RunInstances cancel
# ---------------------------------------------------------------------------


class TestRunInstancesCancel:
    @pytest.mark.parametrize("code", ["RequestLimitExceeded", "UnauthorizedOperation"])
    def test_lookup_client_error_is_error_not_success(self, code):
        handler = _run_instances_handler()
        handler.aws_client.ec2_client.describe_instances.side_effect = _client_error(code)

        result = handler.cancel_resource("r-1", "req-1")

        assert result["status"] == "error"
        assert "may still be running" in result["message"]
        handler.aws_ops.terminate_instances_with_fallback.assert_not_called()
        handler._cleanup_on_zero_capacity.assert_not_called()
        assert _events(handler._logger) == ["aws.cancel_failed"]

    def test_lookup_generic_exception_is_error(self):
        handler = _run_instances_handler()
        handler.aws_client.ec2_client.describe_instances.side_effect = RuntimeError("boom")

        assert handler.cancel_resource("r-1", "req-1")["status"] == "error"
        handler.aws_ops.terminate_instances_with_fallback.assert_not_called()

    def test_retry_exhaustion_wrapper_is_error_with_alert(self):
        handler = _run_instances_handler()

        def exhausted(fn, *a, **kw):
            raise MaxRetriesExceededError(2, _client_error("RequestLimitExceeded"))

        handler._retry_with_backoff = exhausted

        result = handler.cancel_resource("r-1", "req-1")

        assert result["status"] == "error"
        assert "RequestLimitExceeded" in result["message"]
        assert _events(handler._logger) == ["aws.cancel_failed"]

    def test_empty_lookup_is_success_marked_unverified(self):
        handler = _run_instances_handler()
        handler.aws_client.ec2_client.describe_instances.return_value = {"Reservations": []}

        result = handler.cancel_resource("r-1", "req-1")

        assert result["status"] == "success"
        assert "unverified" in result["message"]
        warning = handler._logger.warning.call_args
        assert warning.kwargs["extra"]["event"] == "aws.cancel_unverified"
        handler.aws_ops.terminate_instances_with_fallback.assert_not_called()
        handler._cleanup_on_zero_capacity.assert_not_called()
        assert handler.aws_client.ec2_client.describe_instances.call_count == 1

    def test_reservation_not_found_is_treated_as_empty(self):
        handler = _run_instances_handler()
        handler.aws_client.ec2_client.describe_instances.side_effect = _client_error(
            "InvalidReservationID.NotFound"
        )

        result = handler.cancel_resource("r-1", "req-1")

        assert result["status"] == "success"
        assert "unverified" in result["message"]

    def test_paginated_lookup_terminates_all_pages_and_skips_terminated(self):
        handler = _run_instances_handler()
        page1 = {**_reservation(("i-1", "running"), ("i-t", "terminated")), "NextToken": "tok"}
        page2 = _reservation(("i-2", "pending"))
        handler.aws_client.ec2_client.describe_instances.side_effect = [page1, page2]

        result = handler.cancel_resource("r-1", "req-1")

        assert result["status"] == "success"
        ids = handler.aws_ops.terminate_instances_with_fallback.call_args.args[0]
        assert ids == ["i-1", "i-2"]
        second_call = handler.aws_client.ec2_client.describe_instances.call_args_list[1]
        assert second_call.kwargs["NextToken"] == "tok"
        handler._cleanup_on_zero_capacity.assert_called_once_with("run_instances", "req-1")

    def test_termination_failure_is_error(self):
        handler = _run_instances_handler()
        handler.aws_client.ec2_client.describe_instances.return_value = _reservation(
            ("i-1", "running")
        )
        handler.aws_ops.terminate_instances_with_fallback.side_effect = AWSInfrastructureError(
            "denied"
        )

        result = handler.cancel_resource("r-1", "req-1")

        assert result["status"] == "error"
        handler._cleanup_on_zero_capacity.assert_not_called()

    def test_launch_template_cleanup_failure_after_termination_stays_success(self):
        handler = _run_instances_handler()
        handler.aws_client.ec2_client.describe_instances.return_value = _reservation(
            ("i-1", "running")
        )
        handler._cleanup_on_zero_capacity.side_effect = RuntimeError("lt in use")

        assert handler.cancel_resource("r-1", "req-1")["status"] == "success"


# ---------------------------------------------------------------------------
# Fleet response checks
# ---------------------------------------------------------------------------


class TestCheckUnsuccessfulFleetResponse:
    def _resp(self, key, id_field, code):
        return {key: [{id_field: "f-1", "Error": {"Code": code, "Message": "x"}}]}

    @pytest.mark.parametrize(
        "kind,key,id_field,code",
        [
            ("ec2_fleet", "UnsuccessfulFleetDeletions", "FleetId", "fleetIdDoesNotExist"),
            (
                "spot_fleet",
                "UnsuccessfulFleetRequests",
                "SpotFleetRequestId",
                "fleetRequestIdDoesNotExist",
            ),
        ],
    )
    def test_not_found_is_success(self, kind, key, id_field, code):
        check_unsuccessful_fleet_response(
            self._resp(key, id_field, code), kind, lambda _f: pytest.fail("no describe needed")
        )

    @pytest.mark.parametrize(
        "kind,key,id_field,code",
        [
            ("ec2_fleet", "UnsuccessfulFleetDeletions", "FleetId", "fleetNotInDeletableState"),
            (
                "spot_fleet",
                "UnsuccessfulFleetRequests",
                "SpotFleetRequestId",
                "fleetRequestNotInCancellableState",
            ),
        ],
    )
    def test_not_in_state_requires_describe_confirmation(self, kind, key, id_field, code):
        resp = self._resp(key, id_field, code)
        check_unsuccessful_fleet_response(resp, kind, lambda _f: True)
        with pytest.raises(AWSInfrastructureError):
            check_unsuccessful_fleet_response(resp, kind, lambda _f: False)

        def boom(_f):
            raise RuntimeError("describe failed")

        with pytest.raises(AWSInfrastructureError):
            check_unsuccessful_fleet_response(resp, kind, boom)

    @pytest.mark.parametrize("code", ["unexpectedError", "fleetIdMalformed"])
    def test_other_codes_raise(self, code):
        resp = self._resp("UnsuccessfulFleetDeletions", "FleetId", code)
        with pytest.raises(AWSInfrastructureError, match=code):
            check_unsuccessful_fleet_response(resp, "ec2_fleet", lambda _f: True)

    def test_clean_response_passes(self):
        check_unsuccessful_fleet_response({"UnsuccessfulFleetDeletions": []}, "ec2_fleet", bool)


def _spot_cancel_handler(response):
    handler: Any = SpotFleetHandler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    handler._machine_adapter = None
    mgr = handler._release_manager
    mgr._retry = _direct_retry
    mgr._aws_client.ec2_client.cancel_spot_fleet_requests.return_value = response
    mgr._aws_client.ec2_client.describe_spot_fleet_requests.return_value = {
        "SpotFleetRequestConfigs": [{"SpotFleetRequestState": "active"}]
    }
    mgr._fetch_fleet_details = MagicMock(return_value={})
    mgr._extract_capacity_input = MagicMock(return_value=(MagicMock(), {}))
    mgr._cleanup_launch_template = MagicMock()
    return handler


class TestFleetCancelPartialFailures:
    def test_spot_fleet_partial_failure_is_error(self):
        handler = _spot_cancel_handler(
            {
                "UnsuccessfulFleetRequests": [
                    {
                        "SpotFleetRequestId": "sfr-1",
                        "Error": {"Code": "unexpectedError", "Message": "x"},
                    }
                ]
            }
        )
        result = handler.cancel_resource("sfr-1", "req-1")
        assert result["status"] == "error"
        assert _events(handler._logger) == ["aws.cancel_failed"]

    def test_spot_fleet_not_found_is_success(self):
        handler = _spot_cancel_handler(
            {
                "UnsuccessfulFleetRequests": [
                    {
                        "SpotFleetRequestId": "sfr-1",
                        "Error": {"Code": "fleetRequestIdDoesNotExist", "Message": "x"},
                    }
                ]
            }
        )
        assert handler.cancel_resource("sfr-1", "req-1")["status"] == "success"

    def test_spot_fleet_not_cancellable_but_still_active_is_error(self):
        handler = _spot_cancel_handler(
            {
                "UnsuccessfulFleetRequests": [
                    {
                        "SpotFleetRequestId": "sfr-1",
                        "Error": {"Code": "fleetRequestNotInCancellableState", "Message": "x"},
                    }
                ]
            }
        )
        assert handler.cancel_resource("sfr-1", "req-1")["status"] == "error"

    def _ec2_handler(self, response):
        handler: Any = EC2FleetHandler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        handler._machine_adapter = None
        mgr = handler._fleet_release_manager
        mgr._retry = _direct_retry
        mgr._aws_client.ec2_client.delete_fleets.return_value = response
        mgr._aws_client.ec2_client.describe_fleets.return_value = {
            "Fleets": [{"FleetState": "active"}]
        }
        mgr._fetch_fleet_details = MagicMock(return_value={"Type": "request"})
        mgr._cleanup_launch_template = MagicMock()
        return handler

    def test_ec2_fleet_partial_failure_is_error(self):
        handler = self._ec2_handler(
            {
                "UnsuccessfulFleetDeletions": [
                    {"FleetId": "f-1", "Error": {"Code": "unexpectedError", "Message": "x"}}
                ]
            }
        )
        # Non-maintain cancel goes straight to delete_fleets via the release flow.
        handler._fleet_release_manager._extract_capacity_input = MagicMock(
            return_value=(MagicMock(), {})
        )
        assert handler.cancel_resource("f-1", "req-1")["status"] == "error"

    def test_ec2_maintain_delete_failure_propagates(self):
        handler = self._ec2_handler(
            {
                "UnsuccessfulFleetDeletions": [
                    {"FleetId": "f-1", "Error": {"Code": "unexpectedError", "Message": "x"}}
                ]
            }
        )
        with pytest.raises(AWSInfrastructureError):
            handler._fleet_release_manager._delete_fleet("f-1")


# ---------------------------------------------------------------------------
# ASG delete
# ---------------------------------------------------------------------------


class TestAsgDelete:
    def test_validation_error_with_group_absent_is_success(self):
        client = MagicMock()
        client.delete_auto_scaling_group.side_effect = _client_error("ValidationError")
        client.describe_auto_scaling_groups.return_value = {"AutoScalingGroups": []}

        delete_asg_or_confirm_gone(client, _direct_retry, MagicMock(), "asg-1")

    def test_failure_with_group_present_raises(self):
        client = MagicMock()
        client.delete_auto_scaling_group.side_effect = _client_error("ScalingActivityInProgress")
        client.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"AutoScalingGroupName": "asg-1"}]
        }
        with pytest.raises(AWSInfrastructureError, match="asg-1"):
            delete_asg_or_confirm_gone(client, _direct_retry, MagicMock(), "asg-1")

    def test_retry_exhaustion_wrapper_is_unwrapped_and_raises(self):
        client = MagicMock()
        client.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"AutoScalingGroupName": "asg-1"}]
        }

        def exhausted(fn, *a, **kw):
            raise MaxRetriesExceededError(3, _client_error("ResourceInUse"))

        with pytest.raises(AWSInfrastructureError, match="ResourceInUse"):
            delete_asg_or_confirm_gone(client, exhausted, MagicMock(), "asg-1")

    def test_describe_failure_raises(self):
        client = MagicMock()
        client.delete_auto_scaling_group.side_effect = _client_error("ValidationError")
        client.describe_auto_scaling_groups.side_effect = RuntimeError("down")
        with pytest.raises(AWSInfrastructureError, match="could not verify"):
            delete_asg_or_confirm_gone(client, _direct_retry, MagicMock(), "asg-1")

    def test_transient_failure_then_success_does_not_alert(self):
        client = MagicMock()
        calls = {"n": 0}

        def flaky(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _client_error("ScalingActivityInProgress")

        def retry(fn, *a, operation_type="standard", **kw):
            try:
                return fn(**kw)
            except ClientError:
                return fn(**kw)

        client.delete_auto_scaling_group.side_effect = flaky
        logger = MagicMock()
        delete_asg_or_confirm_gone(client, retry, logger, "asg-1")
        assert calls["n"] == 2
        logger.error.assert_not_called()

    def test_asg_cancel_returns_error_dict_when_group_survives(self):
        handler: Any = ASGHandler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        handler._retry_with_backoff = _direct_retry
        asg = handler.aws_client.autoscaling_client
        asg.delete_auto_scaling_group.side_effect = _client_error("ResourceInUse")
        asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"AutoScalingGroupName": "asg-1"}]
        }

        result = handler.cancel_resource("asg-1", "req-1")

        assert result["status"] == "error"
        assert _events(handler._logger) == ["aws.cancel_failed"]

    def test_post_terminate_delete_failure_alerts_once_and_does_not_raise(self):
        logger = MagicMock()
        mgr = ASGCapacityManager(
            aws_client=MagicMock(),
            aws_ops=MagicMock(),
            request_adapter=None,
            cleanup_on_zero_capacity_fn=MagicMock(),
            logger=logger,
            retry_with_backoff=_direct_retry,
            chunk_list=lambda lst, n: [lst],
        )
        client = mgr._aws_client.autoscaling_client
        client.delete_auto_scaling_group.side_effect = _client_error("ResourceInUse")
        client.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"AutoScalingGroupName": "asg-1"}]
        }

        mgr._call_delete_asg("asg-1")

        assert _events(logger) == ["aws.fleet_delete_failed"]


# ---------------------------------------------------------------------------
# Post-terminate fleet teardown
# ---------------------------------------------------------------------------


class TestPostTerminateFleetTeardown:
    def _manager(self) -> SpotFleetReleaseManager:
        return SpotFleetReleaseManager(
            aws_client=MagicMock(),
            aws_ops=MagicMock(),
            request_adapter=None,
            cleanup_on_zero_capacity_fn=MagicMock(),
            logger=MagicMock(),
            retry_fn=_direct_retry,
        )

    def test_instant_fleet_delete_failure_alerts_once_and_release_continues(self):
        mgr = self._manager()
        mgr._aws_client.ec2_client.cancel_spot_fleet_requests.side_effect = _client_error(
            "UnauthorizedOperation"
        )
        mgr._cleanup_launch_template = MagicMock()
        decision = MagicMock(has_fleet_record=False, requires_capacity_reduction=False)

        mgr._handle_post_terminate("f-1", {}, decision, False, {}, "req-1", "Spot Fleet")

        assert _events(mgr._logger) == ["aws.fleet_delete_failed"]
        mgr._cleanup_launch_template.assert_called_once()

    def test_fleet_record_delete_failure_alerts_and_skips_launch_template(self):
        mgr = self._manager()
        mgr._aws_client.ec2_client.cancel_spot_fleet_requests.return_value = {
            "UnsuccessfulFleetRequests": [
                {"SpotFleetRequestId": "f-1", "Error": {"Code": "unexpectedError", "Message": "x"}}
            ]
        }
        mgr._cleanup_launch_template = MagicMock()
        decision = MagicMock(has_fleet_record=True, requires_capacity_reduction=False)

        mgr._handle_post_terminate("f-1", {}, decision, True, {}, "req-1", "Spot Fleet")

        assert _events(mgr._logger) == ["aws.fleet_delete_failed"]
        mgr._cleanup_launch_template.assert_not_called()

    def test_transient_failure_then_success_does_not_alert(self):
        mgr = self._manager()
        attempts = {"n": 0}

        def retry(fn, operation_type="standard", **kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                try:
                    return fn(**kw)
                except ClientError:
                    pass
            return {}

        mgr._retry = retry
        mgr._aws_client.ec2_client.cancel_spot_fleet_requests.side_effect = _client_error(
            "RequestLimitExceeded"
        )
        mgr._cleanup_launch_template = MagicMock()
        decision = MagicMock(has_fleet_record=True, requires_capacity_reduction=False)

        mgr._handle_post_terminate("f-1", {}, decision, True, {}, "req-1", "Spot Fleet")

        assert _events(mgr._logger) == []
        mgr._cleanup_launch_template.assert_called_once()


class TestAlertContent:
    def test_request_id_and_region_are_in_message_and_fields(self):
        logger = MagicMock()
        emit_alert(
            logger,
            "aws.cancel_failed",
            "Failed to cancel",
            resource_type="asg",
            resource_id="asg-1",
            request_id="req-9",
            region="eu-west-1",
            reason="denied",
        )
        text = logger.error.call_args.args[1]
        assert "request_id=req-9" in text
        assert "region=eu-west-1" in text
        extra = logger.error.call_args.kwargs["extra"]
        assert extra["region"] == "eu-west-1"
        assert extra["request_id"] == "req-9"

    def test_asg_delete_alert_carries_real_request_id_and_skips_template_cleanup(self):
        handler: Any = ASGHandler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        handler._retry_with_backoff = _direct_retry
        handler.config_port = MagicMock()
        handler.config_port.get_resource_prefix.return_value = "orb-asg-"
        asg = handler.aws_client.autoscaling_client
        asg.delete_auto_scaling_group.side_effect = _client_error("ResourceInUse")
        asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"AutoScalingGroupName": "orb-asg-req-5"}]
        }
        handler._cleanup_on_zero_capacity = MagicMock()
        mgr = handler._capacity_manager

        assert mgr._call_delete_asg("orb-asg-req-5") is False

        extra = handler._logger.error.call_args.kwargs["extra"]
        assert extra["request_id"] == "req-5"

    def test_asg_delete_in_progress_counts_as_gone(self):
        client = MagicMock()
        client.delete_auto_scaling_group.side_effect = _client_error("ScalingActivityInProgress")
        client.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"Status": "Delete in progress"}]
        }
        delete_asg_or_confirm_gone(client, _direct_retry, MagicMock(), "asg-1")


class TestMaintainFleetSurvival:
    def test_maintain_fleet_delete_failure_after_terminate_raises(self):
        mgr = SpotFleetReleaseManager(
            aws_client=MagicMock(),
            aws_ops=MagicMock(),
            request_adapter=None,
            cleanup_on_zero_capacity_fn=MagicMock(),
            logger=MagicMock(),
            retry_fn=_direct_retry,
        )
        mgr._aws_client.ec2_client.cancel_spot_fleet_requests.side_effect = _client_error(
            "UnauthorizedOperation"
        )
        mgr._cleanup_launch_template = MagicMock()
        decision = MagicMock(has_fleet_record=True, requires_capacity_reduction=True)

        with pytest.raises(AWSInfrastructureError, match="may relaunch"):
            mgr._handle_post_terminate("f-1", {}, decision, True, {}, "req-1", "Spot Fleet")

        assert _events(mgr._logger) == ["aws.fleet_delete_failed"]
        mgr._cleanup_launch_template.assert_not_called()


class TestErrorCodeOf:
    def test_client_error_code(self):
        from orb.providers.aws.infrastructure.handlers.shared.delete_outcome import error_code_of

        assert error_code_of(_client_error("InvalidReservationID.NotFound")) == (
            "InvalidReservationID.NotFound"
        )

    def test_retry_wrapper_is_unwrapped(self):
        from orb.providers.aws.infrastructure.handlers.shared.delete_outcome import error_code_of

        wrapped = MaxRetriesExceededError(2, _client_error("RequestLimitExceeded"))
        assert error_code_of(wrapped) == "RequestLimitExceeded"

    def test_domain_error_code_and_unknown(self):
        from orb.providers.aws.exceptions.aws_exceptions import AWSError
        from orb.providers.aws.infrastructure.handlers.shared.delete_outcome import error_code_of

        assert error_code_of(AWSError("x", aws_error_code="Throttling")) == "Throttling"
        assert error_code_of(RuntimeError("x")) == ""

    def test_warning_level_alert_uses_warning(self):
        logger = MagicMock()
        emit_alert(
            logger,
            "aws.cancel_unverified",
            "m",
            resource_type="x",
            resource_id="y",
            level="warning",
        )
        logger.warning.assert_called_once()
        logger.error.assert_not_called()


class TestFleetDeleteAlertCarriesRequestId:
    """Return paths do not know the request; the fleet's tag supplies it."""

    def _spot_release(self, fleet_details):
        mgr = SpotFleetReleaseManager(
            aws_client=MagicMock(),
            aws_ops=MagicMock(),
            request_adapter=None,
            cleanup_on_zero_capacity_fn=MagicMock(),
            logger=MagicMock(),
            retry_fn=_direct_retry,
        )
        mgr._aws_client.ec2_client.cancel_spot_fleet_requests.side_effect = _client_error(
            "UnauthorizedOperation"
        )
        mgr._aws_client.ec2_client.describe_spot_fleet_instances.return_value = {
            "ActiveInstances": []
        }
        mgr._aws_client.ec2_client.describe_spot_fleet_requests.return_value = {
            "SpotFleetRequestConfigs": []
        }
        mgr._cleanup_launch_template = MagicMock()
        mgr.release("sfr-1", ["i-1"], fleet_details)
        return mgr

    def test_spot_fleet_alert_uses_tag_request_id(self):
        details = {
            "SpotFleetRequestConfig": {
                "Type": "request",
                "TargetCapacity": 1,
                "TagSpecifications": [{"Tags": [{"Key": "orb:request-id", "Value": "req-spot-7"}]}],
            },
            "Tags": [],
        }
        mgr = self._spot_release(details)
        alerts = [
            c.kwargs["extra"]
            for c in mgr._logger.error.call_args_list
            if "extra" in c.kwargs and c.kwargs["extra"].get("event") == "aws.fleet_delete_failed"
        ]
        assert [a["request_id"] for a in alerts] == ["req-spot-7"]

    def test_ec2_fleet_alert_uses_tag_request_id(self):
        from orb.providers.aws.infrastructure.handlers.ec2_fleet.release_manager import (
            EC2FleetReleaseManager,
        )

        mgr = EC2FleetReleaseManager(
            aws_client=MagicMock(),
            aws_ops=MagicMock(),
            request_adapter=None,
            config_port=None,
            logger=MagicMock(),
            retry_fn=_direct_retry,
            paginate_fn=MagicMock(return_value=[]),
            collect_with_next_token_fn=MagicMock(return_value=[]),
            cleanup_on_zero_capacity_fn=MagicMock(),
        )
        mgr._aws_client.ec2_client.delete_fleets.side_effect = _client_error(
            "UnauthorizedOperation"
        )
        mgr._cleanup_launch_template = MagicMock()
        details = {
            "Type": "request",
            "TargetCapacitySpecification": {"TotalTargetCapacity": 1},
            "Tags": [{"Key": "orb:request-id", "Value": "req-ec2-3"}],
        }

        mgr.release("fleet-1", ["i-1"], details)

        alerts = [
            c.kwargs["extra"]
            for c in mgr._logger.error.call_args_list
            if "extra" in c.kwargs and c.kwargs["extra"].get("event") == "aws.fleet_delete_failed"
        ]
        assert [a["request_id"] for a in alerts] == ["req-ec2-3"]


class TestProviderCancelIsNotWired:
    def test_application_layer_does_not_call_provider_cancel(self):
        """Provider-side cancel has no production caller yet.

        Request cancellation only returns known machines and flips state;
        aborting in-flight fleets that have no machines yet is not wired. If
        a caller is added, this guard must be revisited together with the
        error contract of ``cancel_resource``.
        """
        from pathlib import Path

        import orb

        root = Path(orb.__file__).parent
        offenders = [
            str(path.relative_to(root))
            for sub in ("application", "api", "interface")
            for path in (root / sub).rglob("*.py")
            if "cancel_fleet_request" in path.read_text(encoding="utf-8")
            or ".cancel_resource(" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

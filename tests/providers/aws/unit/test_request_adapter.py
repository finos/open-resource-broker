"""Unit tests for AWSRequestAdapter.

Covers get_request_status, terminate_instances, and cancel_fleet_request
across all provider API types and error paths.

All AWS client calls are replaced with MagicMock — no real AWS connections.
"""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from orb.domain.request.request_types import RequestType
from orb.providers.aws.infrastructure.adapters.request_adapter import AWSRequestAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_error(code: str, message: str = "some message") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Op")


def _make_adapter(handler_factory=None):
    aws_client = MagicMock()
    logger = MagicMock()
    return AWSRequestAdapter(aws_client=aws_client, logger=logger, handler_factory=handler_factory)


def _make_request(
    request_type=RequestType.ACQUIRE,
    provider_api="EC2Fleet",
    resource_id="res-001",
):
    req = MagicMock()
    req.request_type = request_type
    req.provider_api = provider_api
    req.resource_ids = [resource_id] if resource_id else []
    req.request_id = "req-001"
    # resource_id property returns first element
    type(req).resource_id = property(
        lambda self: self.resource_ids[0] if self.resource_ids else None
    )
    return req


# ---------------------------------------------------------------------------
# get_request_status — missing resource_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetRequestStatusMissingResourceId:
    def test_returns_unknown_when_no_resource_id(self):
        adapter = _make_adapter()
        req = _make_request(resource_id=None)
        result = adapter.get_request_status(req)
        assert result["status"] == "unknown"
        assert "No resource ID" in result["message"]

    def test_returns_unknown_for_unknown_request_type(self):
        adapter = _make_adapter()
        req = _make_request(resource_id="res-001")
        req.request_type = "UNSUPPORTED"  # type: ignore[assignment]
        result = adapter.get_request_status(req)
        assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# _get_acquire_request_status dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAcquireRequestStatusDispatch:
    def test_unknown_provider_api_returns_unknown(self):
        adapter = _make_adapter()
        req = _make_request(provider_api="FancyCloudX")
        result = adapter.get_request_status(req)
        assert result["status"] == "unknown"
        assert "FancyCloudX" in result["message"]


# ---------------------------------------------------------------------------
# EC2 Fleet status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetEc2FleetStatus:
    def _setup_fleet(self, adapter, fleet_state="active", instances=None):
        fleet = {
            "FleetState": fleet_state,
            "TargetCapacitySpecification": {"TotalTargetCapacity": 2},
            "FulfilledCapacity": 2.0,
            "ActivityStatus": "fulfilled",
            "Errors": [],
        }
        adapter._aws_client.ec2_client.describe_fleets.return_value = {"Fleets": [fleet]}
        adapter._aws_client.ec2_client.describe_fleet_instances.return_value = {
            "ActiveInstances": instances or []
        }
        return fleet

    def test_returns_fleet_state(self):
        adapter = _make_adapter()
        self._setup_fleet(adapter, fleet_state="active")
        req = _make_request(provider_api="EC2Fleet", resource_id="fleet-001")
        result = adapter.get_request_status(req)
        assert result["status"] == "active"

    def test_returns_fulfilled_capacity(self):
        adapter = _make_adapter()
        self._setup_fleet(adapter, fleet_state="active")
        req = _make_request(provider_api="EC2Fleet", resource_id="fleet-001")
        result = adapter.get_request_status(req)
        assert result["fulfilled_capacity"] == 2.0

    def test_fulfilled_capacity_defaults_to_zero_when_absent(self):
        adapter = _make_adapter()
        fleet = {
            "FleetState": "active",
            "TargetCapacitySpecification": {"TotalTargetCapacity": 1},
            "ActivityStatus": "pending_fulfillment",
            "Errors": [],
        }
        adapter._aws_client.ec2_client.describe_fleets.return_value = {"Fleets": [fleet]}
        adapter._aws_client.ec2_client.describe_fleet_instances.return_value = {
            "ActiveInstances": []
        }
        req = _make_request(provider_api="EC2Fleet")
        result = adapter.get_request_status(req)
        assert result["fulfilled_capacity"] == 0

    def test_fleet_not_found_returns_error(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.describe_fleets.return_value = {"Fleets": []}
        req = _make_request(provider_api="EC2Fleet", resource_id="fleet-missing")
        result = adapter.get_request_status(req)
        assert result["status"] == "error"
        assert "fleet-missing" in result["message"]

    def test_returns_instance_list(self):
        adapter = _make_adapter()
        instances = [
            {"InstanceId": "i-001", "InstanceType": "t3.medium", "InstanceLifecycle": "on-demand"},
        ]
        self._setup_fleet(adapter, instances=instances)
        req = _make_request(provider_api="EC2Fleet")
        result = adapter.get_request_status(req)
        assert len(result["instances"]) == 1
        assert result["instances"][0]["instance_id"] == "i-001"
        assert result["instances"][0]["lifecycle"] == "on-demand"

    def test_api_exception_returns_error(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.describe_fleets.side_effect = RuntimeError("boom")
        req = _make_request(provider_api="EC2Fleet")
        result = adapter.get_request_status(req)
        assert result["status"] == "error"
        assert "boom" in result["message"]


# ---------------------------------------------------------------------------
# Spot Fleet status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSpotFleetStatus:
    def _setup_spot_fleet(self, adapter, state="active", instances=None):
        sfr_config = {
            "SpotFleetRequestState": state,
            "SpotFleetRequestConfig": {"TargetCapacity": 3},
        }
        adapter._aws_client.ec2_client.describe_spot_fleet_requests.return_value = {
            "SpotFleetRequestConfigs": [sfr_config]
        }
        adapter._aws_client.ec2_client.describe_spot_fleet_instances.return_value = {
            "ActiveInstances": instances or []
        }

    def test_returns_spot_fleet_state(self):
        adapter = _make_adapter()
        self._setup_spot_fleet(adapter, state="active")
        req = _make_request(provider_api="SpotFleet")
        result = adapter.get_request_status(req)
        assert result["status"] == "active"

    def test_not_found_returns_error(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.describe_spot_fleet_requests.return_value = {
            "SpotFleetRequestConfigs": []
        }
        req = _make_request(provider_api="SpotFleet", resource_id="sfr-missing")
        result = adapter.get_request_status(req)
        assert result["status"] == "error"
        assert "sfr-missing" in result["message"]

    def test_instances_have_spot_lifecycle(self):
        adapter = _make_adapter()
        instances = [{"InstanceId": "i-spot", "InstanceType": "r5.xlarge"}]
        self._setup_spot_fleet(adapter, instances=instances)
        req = _make_request(provider_api="SpotFleet")
        result = adapter.get_request_status(req)
        assert result["instances"][0]["lifecycle"] == "spot"

    def test_api_exception_returns_error(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.describe_spot_fleet_requests.side_effect = RuntimeError("x")
        req = _make_request(provider_api="SpotFleet")
        result = adapter.get_request_status(req)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# ASG status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAsgStatus:
    def _setup_asg(self, adapter, instances=None):
        asg = {
            "DesiredCapacity": 2,
            "MinSize": 1,
            "MaxSize": 5,
            "Instances": instances or [],
        }
        adapter._aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [asg]
        }

    def test_returns_active_status(self):
        adapter = _make_adapter()
        self._setup_asg(adapter)
        req = _make_request(provider_api="ASG")
        result = adapter.get_request_status(req)
        assert result["status"] == "active"

    def test_returns_capacity_info(self):
        adapter = _make_adapter()
        self._setup_asg(adapter)
        req = _make_request(provider_api="ASG")
        result = adapter.get_request_status(req)
        assert result["target_capacity"] == 2
        assert result["min_size"] == 1
        assert result["max_size"] == 5

    def test_asg_not_found_returns_error(self):
        adapter = _make_adapter()
        adapter._aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": []
        }
        req = _make_request(provider_api="ASG", resource_id="asg-missing")
        result = adapter.get_request_status(req)
        assert result["status"] == "error"
        assert "asg-missing" in result["message"]

    def test_instances_included(self):
        adapter = _make_adapter()
        instances = [
            {"InstanceId": "i-asg-001", "LifecycleState": "InService", "HealthStatus": "Healthy"}
        ]
        self._setup_asg(adapter, instances=instances)
        req = _make_request(provider_api="ASG")
        result = adapter.get_request_status(req)
        assert result["instances"][0]["instance_id"] == "i-asg-001"

    def test_api_exception_returns_error(self):
        adapter = _make_adapter()
        adapter._aws_client.autoscaling_client.describe_auto_scaling_groups.side_effect = (
            RuntimeError("asg err")
        )
        req = _make_request(provider_api="ASG")
        result = adapter.get_request_status(req)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# RunInstances status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetRunInstancesStatus:
    def _setup_describe(self, adapter, states=None):
        states = states or ["running"]
        instances = [
            {
                "InstanceId": f"i-{i:03d}",
                "State": {"Name": s},
                "InstanceType": "t3.micro",
                "PrivateIpAddress": "10.0.0.1",
            }
            for i, s in enumerate(states)
        ]
        adapter._aws_client.ec2_client.describe_instances.return_value = {
            "Reservations": [{"Instances": instances}]
        }

    def test_returns_active_when_instances_found(self):
        adapter = _make_adapter()
        self._setup_describe(adapter, states=["running"])
        req = _make_request(provider_api="RunInstances", resource_id="i-001")
        result = adapter.get_request_status(req)
        assert result["status"] == "active"

    def test_returns_error_when_no_instances(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.describe_instances.return_value = {"Reservations": []}
        req = _make_request(provider_api="RunInstances", resource_id="i-001")
        result = adapter.get_request_status(req)
        assert result["status"] == "error"

    def test_splits_comma_separated_resource_id(self):
        adapter = _make_adapter()
        self._setup_describe(adapter, states=["running", "running"])
        req = _make_request(provider_api="RunInstances", resource_id="i-001,i-002")
        adapter.get_request_status(req)
        call_args = adapter._aws_client.ec2_client.describe_instances.call_args
        assert call_args[1]["InstanceIds"] == ["i-001", "i-002"]

    def test_api_exception_returns_error(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.describe_instances.side_effect = RuntimeError("err")
        req = _make_request(provider_api="RunInstances", resource_id="i-001")
        result = adapter.get_request_status(req)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Return request status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetReturnRequestStatus:
    def _setup_describe(self, adapter, states):
        instances = [
            {
                "InstanceId": f"i-{i:03d}",
                "State": {"Name": s},
                "InstanceType": "t3.micro",
            }
            for i, s in enumerate(states)
        ]
        adapter._aws_client.ec2_client.describe_instances.return_value = {
            "Reservations": [{"Instances": instances}]
        }

    def test_complete_when_all_terminated(self):
        adapter = _make_adapter()
        self._setup_describe(adapter, states=["terminated", "terminated"])
        req = _make_request(
            request_type=RequestType.RETURN,
            provider_api="RunInstances",
            resource_id="i-001,i-002",
        )
        result = adapter.get_request_status(req)
        assert result["status"] == "complete"

    def test_in_progress_when_not_all_terminated(self):
        adapter = _make_adapter()
        self._setup_describe(adapter, states=["running", "terminated"])
        req = _make_request(
            request_type=RequestType.RETURN,
            provider_api="RunInstances",
            resource_id="i-001,i-002",
        )
        result = adapter.get_request_status(req)
        assert result["status"] == "in_progress"

    def test_missing_resource_id_returns_error(self):
        adapter = _make_adapter()
        req = _make_request(request_type=RequestType.RETURN, resource_id=None)
        result = adapter.get_request_status(req)
        assert result["status"] == "unknown"

    def test_api_exception_returns_error(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.describe_instances.side_effect = RuntimeError("boom")
        req = _make_request(request_type=RequestType.RETURN, resource_id="i-001")
        result = adapter.get_request_status(req)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# terminate_instances
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTerminateInstances:
    def test_returns_success_with_terminating_instances(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.terminate_instances.return_value = {
            "TerminatingInstances": [
                {
                    "InstanceId": "i-001",
                    "PreviousState": {"Name": "running"},
                    "CurrentState": {"Name": "shutting-down"},
                }
            ]
        }
        result = adapter.terminate_instances(["i-001"])
        assert result["status"] == "success"
        assert len(result["terminated_instances"]) == 1
        assert result["terminated_instances"][0]["instance_id"] == "i-001"

    def test_invalid_instance_id_treated_as_success(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.terminate_instances.side_effect = ClientError(
            {"Error": {"Code": "InvalidInstanceID.NotFound", "Message": "not found"}},
            "TerminateInstances",
        )
        result = adapter.terminate_instances(["i-gone"])
        assert result["status"] == "success"
        assert result["terminated_instances"] == []

    def test_other_client_error_returns_error(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.terminate_instances.side_effect = ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "no auth"}},
            "TerminateInstances",
        )
        result = adapter.terminate_instances(["i-001"])
        assert result["status"] == "error"
        assert "no auth" in result["message"]

    def test_generic_exception_returns_error(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.terminate_instances.side_effect = RuntimeError("bang")
        result = adapter.terminate_instances(["i-001"])
        assert result["status"] == "error"
        assert "bang" in result["message"]


# ---------------------------------------------------------------------------
# cancel_fleet_request — precondition checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelFleetRequestPreconditions:
    def test_missing_provider_api_returns_error(self):
        adapter = _make_adapter()
        req = _make_request(provider_api=None, resource_id="res-001")
        result = adapter.cancel_fleet_request(req)
        assert result["status"] == "error"
        assert "provider API" in result["message"]

    def test_missing_resource_id_returns_error(self):
        adapter = _make_adapter()
        req = _make_request(provider_api="EC2Fleet", resource_id=None)
        result = adapter.cancel_fleet_request(req)
        assert result["status"] == "error"
        assert "resource ID" in result["message"]

    def test_delegates_to_handler_factory_when_present(self):
        handler = MagicMock()
        handler.cancel_resource.return_value = {"status": "success"}
        factory = MagicMock()
        factory.create_handler.return_value = handler

        adapter = _make_adapter(handler_factory=factory)
        req = _make_request(provider_api="EC2Fleet", resource_id="fleet-001")
        result = adapter.cancel_fleet_request(req)
        factory.create_handler.assert_called_once_with("EC2Fleet")
        handler.cancel_resource.assert_called_once()
        assert result["status"] == "success"

    def test_handler_factory_exception_returns_error(self):
        factory = MagicMock()
        factory.create_handler.side_effect = RuntimeError("factory broke")
        adapter = _make_adapter(handler_factory=factory)
        req = _make_request(provider_api="EC2Fleet", resource_id="fleet-001")
        result = adapter.cancel_fleet_request(req)
        assert result["status"] == "error"
        assert "factory broke" in result["message"]


# ---------------------------------------------------------------------------
# cancel_fleet_request — _cancel_direct paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelDirect:
    def _req_no_factory(self, provider_api, resource_id="res-001"):
        return _make_request(provider_api=provider_api, resource_id=resource_id)

    def test_ec2_fleet_cancel_returns_success(self):
        adapter = _make_adapter()  # no handler_factory
        adapter._aws_client.ec2_client.delete_fleets.return_value = {
            "SuccessfulFleetDeletions": [{"FleetId": "fleet-001"}],
            "UnsuccessfulFleetDeletions": [],
        }
        req = self._req_no_factory("EC2Fleet", "fleet-001")
        result = adapter.cancel_fleet_request(req)
        assert result["status"] == "success"
        assert "fleet-001" in result["successful_fleets"]

    def test_ec2_fleet_with_unsuccessful_deletions(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.delete_fleets.return_value = {
            "SuccessfulFleetDeletions": [],
            "UnsuccessfulFleetDeletions": [
                {"FleetId": "fleet-bad", "Error": {"Message": "already deleted"}}
            ],
        }
        req = self._req_no_factory("EC2Fleet", "fleet-bad")
        result = adapter.cancel_fleet_request(req)
        assert result["status"] == "success"
        assert len(result["unsuccessful_fleets"]) == 1

    def test_spot_fleet_cancel_returns_success(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.cancel_spot_fleet_requests.return_value = {
            "SuccessfulFleetRequests": [{"SpotFleetRequestId": "sfr-001"}],
            "UnsuccessfulFleetRequests": [],
        }
        req = self._req_no_factory("SpotFleet", "sfr-001")
        result = adapter.cancel_fleet_request(req)
        assert result["status"] == "success"
        assert "sfr-001" in result["successful_fleets"]

    def test_asg_cancel_returns_success(self):
        adapter = _make_adapter()
        adapter._aws_client.autoscaling_client.delete_auto_scaling_group.return_value = {}
        req = self._req_no_factory("ASG", "my-asg")
        result = adapter.cancel_fleet_request(req)
        assert result["status"] == "success"
        assert "my-asg" in result["message"]

    def test_unsupported_provider_api_returns_error(self):
        adapter = _make_adapter()
        req = self._req_no_factory("FancyCloud", "res-001")
        result = adapter.cancel_fleet_request(req)
        assert result["status"] == "error"
        assert "Unsupported" in result["message"]

    def test_exception_during_direct_cancel_returns_error(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.delete_fleets.side_effect = RuntimeError("net err")
        req = self._req_no_factory("EC2Fleet", "fleet-001")
        result = adapter.cancel_fleet_request(req)
        assert result["status"] == "error"
        assert "net err" in result["message"]

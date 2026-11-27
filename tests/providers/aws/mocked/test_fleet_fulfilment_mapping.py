"""Fleet fulfilment status-mapping tests at the mocked tier.

check_hosts_status for ASG, EC2Fleet, and SpotFleet is exercised end-to-end
against real moto-provisioned instances — not just for return type. Each test
provisions a fleet, then verifies that the describe -> format -> count status
mapping populates per-instance data and computes the correct fulfilment verdict
(running_count, target_units, state) for the running instances the API reports.

A dedicated shortfall test terminates instances behind an EC2Fleet in
``maintain`` mode so the fulfilled capacity drops below the target, exercising
the ``running_count < target_units`` partial/in-progress path that the
fulfilment state machine resolves.
"""

import pytest

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from tests.providers.aws.mocked.test_provision_lifecycle import (
    _asg_template,
    _ec2_fleet_template,
    _make_aws_client,
    _make_config_port,
    _make_factory,
    _make_logger,
    _make_request,
    _run_instances_template,
    _spot_fleet_template,
)

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Fixtures (mirror test_provision_lifecycle — moto-backed handler factory)
# ---------------------------------------------------------------------------


@pytest.fixture
def aws_client(moto_aws):
    return _make_aws_client()


@pytest.fixture
def factory(aws_client):
    return _make_factory(aws_client, _make_logger(), _make_config_port())


@pytest.fixture
def subnet_id(moto_vpc_resources):
    return moto_vpc_resources["subnet_ids"][0]


@pytest.fixture
def sg_id(moto_vpc_resources):
    return moto_vpc_resources["sg_id"]


def _instance_status_keys_present(result: CheckHostsStatusResult) -> None:
    """Every populated status entry carries instance_id + status."""
    for entry in result.instances:
        assert "instance_id" in entry, f"status entry missing instance_id: {entry}"
        assert "status" in entry, f"status entry missing status: {entry}"


# ---------------------------------------------------------------------------
# ASG — real instance-data status mapping
# ---------------------------------------------------------------------------


class TestASGStatusMapping:
    def test_check_status_populates_running_instances(self, factory, subnet_id, sg_id):
        """ASG check_hosts_status returns one populated entry per InService instance."""
        handler = factory.create_handler("ASG")
        template = _asg_template(subnet_id, sg_id)
        request = _make_request(request_id="map-asg-001", requested_count=2)

        acquire_result = handler.acquire_hosts(request, template)
        asg_name = acquire_result["resource_ids"][0]

        status_request = _make_request(
            request_id="map-asg-001", requested_count=2, resource_ids=[asg_name]
        )
        result = handler.check_hosts_status(status_request)

        assert isinstance(result, CheckHostsStatusResult)
        assert len(result.instances) == 2
        _instance_status_keys_present(result)
        assert all(entry["status"] in ("running", "pending") for entry in result.instances)
        # Every entry is stamped with the owning ASG.
        assert all(entry["resource_id"] == asg_name for entry in result.instances)

    def test_check_status_fulfilment_counts(self, factory, subnet_id, sg_id):
        """ASG fulfilment reports running_count == target == requested and state fulfilled."""
        handler = factory.create_handler("ASG")
        template = _asg_template(subnet_id, sg_id)
        request = _make_request(request_id="map-asg-002", requested_count=2)

        asg_name = handler.acquire_hosts(request, template)["resource_ids"][0]
        status_request = _make_request(
            request_id="map-asg-002", requested_count=2, resource_ids=[asg_name]
        )
        result = handler.check_hosts_status(status_request)

        assert result.fulfilment.running_count == 2
        assert result.fulfilment.target_units == 2
        assert result.fulfilment.state == "fulfilled"


# ---------------------------------------------------------------------------
# EC2Fleet — real instance-data status mapping
# ---------------------------------------------------------------------------


class TestEC2FleetStatusMapping:
    @pytest.mark.parametrize("fleet_type", ["instant", "maintain"])
    def test_check_status_populates_running_instances(self, factory, subnet_id, sg_id, fleet_type):
        """EC2Fleet check_hosts_status returns one populated entry per active instance."""
        handler = factory.create_handler("EC2Fleet")
        template = _ec2_fleet_template(subnet_id, sg_id, fleet_type=fleet_type)
        request = _make_request(request_id=f"map-fleet-{fleet_type}", requested_count=2)

        acquire_result = handler.acquire_hosts(request, template)
        fleet_id = acquire_result["resource_ids"][0]

        status_request = _make_request(
            request_id=f"map-fleet-{fleet_type}", requested_count=2, resource_ids=[fleet_id]
        )
        status_request.metadata = acquire_result.get("provider_data", {})
        result = handler.check_hosts_status(status_request)

        assert isinstance(result, CheckHostsStatusResult)
        assert len(result.instances) == 2
        _instance_status_keys_present(result)
        assert all(entry["resource_id"] == fleet_id for entry in result.instances)
        assert result.fulfilment.running_count == 2
        assert result.fulfilment.target_units == 2
        assert result.fulfilment.state == "fulfilled"

    def test_maintain_fleet_shortfall_is_not_fulfilled(self, factory, subnet_id, sg_id, ec2_client):
        """Terminating an active instance drops a maintain fleet below target.

        The remaining running instances are still reported with populated data,
        but the fulfilment verdict must reflect the shortfall
        (running_count < target_units, state not fulfilled).
        """
        handler = factory.create_handler("EC2Fleet")
        template = _ec2_fleet_template(subnet_id, sg_id, fleet_type="maintain")
        request = _make_request(request_id="map-fleet-shortfall", requested_count=3)

        fleet_id = handler.acquire_hosts(request, template)["resource_ids"][0]

        active = ec2_client.describe_fleet_instances(FleetId=fleet_id)["ActiveInstances"]
        active_ids = [i["InstanceId"] for i in active]
        assert len(active_ids) == 3
        ec2_client.terminate_instances(InstanceIds=active_ids[:1])

        status_request = _make_request(
            request_id="map-fleet-shortfall", requested_count=3, resource_ids=[fleet_id]
        )
        result = handler.check_hosts_status(status_request)

        assert result.fulfilment.target_units == 3
        assert result.fulfilment.running_count == 2
        assert result.fulfilment.running_count < result.fulfilment.target_units
        assert result.fulfilment.state != "fulfilled"


# ---------------------------------------------------------------------------
# SpotFleet — real instance-data status mapping
# ---------------------------------------------------------------------------


class TestSpotFleetStatusMapping:
    @pytest.mark.parametrize("fleet_type", ["request", "maintain"])
    def test_check_status_populates_running_instances(self, factory, subnet_id, sg_id, fleet_type):
        """SpotFleet check_hosts_status returns one populated entry per active instance."""
        handler = factory.create_handler("SpotFleet")
        template = _spot_fleet_template(subnet_id, sg_id, fleet_type=fleet_type)
        request = _make_request(request_id=f"map-spot-{fleet_type}", requested_count=2)

        acquire_result = handler.acquire_hosts(request, template)
        fleet_id = acquire_result["resource_ids"][0]

        status_request = _make_request(
            request_id=f"map-spot-{fleet_type}", requested_count=2, resource_ids=[fleet_id]
        )
        result = handler.check_hosts_status(status_request)

        assert isinstance(result, CheckHostsStatusResult)
        assert len(result.instances) == 2
        _instance_status_keys_present(result)
        assert all(entry["resource_id"] == fleet_id for entry in result.instances)
        assert result.fulfilment.running_count == 2
        assert result.fulfilment.target_units == 2
        assert result.fulfilment.state == "fulfilled"


# ---------------------------------------------------------------------------
# RunInstances — shortfall reference (the only API where moto historically
# fulfilled). Kept alongside the fleet cases to document the shared contract.
# ---------------------------------------------------------------------------


class TestRunInstancesShortfall:
    def test_terminated_instance_produces_shortfall(self, factory, subnet_id, sg_id, ec2_client):
        """Terminating a launched instance leaves the request below its target."""
        handler = factory.create_handler("RunInstances")
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="map-run-shortfall", requested_count=3)

        acquire_result = handler.acquire_hosts(request, template)
        instance_ids = acquire_result["provider_data"]["instance_ids"]
        reservation_id = acquire_result["resource_ids"][0]
        ec2_client.terminate_instances(InstanceIds=instance_ids[:1])

        status_request = _make_request(
            request_id="map-run-shortfall",
            requested_count=3,
            resource_ids=[reservation_id],
            provider_data={"instance_ids": instance_ids, "reservation_id": reservation_id},
        )
        result = handler.check_hosts_status(status_request)

        assert result.fulfilment.target_units == 3
        assert result.fulfilment.running_count < 3
        assert result.fulfilment.state != "fulfilled"

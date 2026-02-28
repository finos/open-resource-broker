"""Moto-based tests for EC2FleetHandler — creation, status, and termination."""

import pytest

from tests.aws_mock.conftest import (
    _make_aws_client,
    _make_config_port,
    _make_logger,
    make_aws_template,
    make_ec2_fleet_handler,
    make_request,
)


@pytest.fixture
def handler(moto_aws):
    aws_client = _make_aws_client()
    logger = _make_logger()
    config_port = _make_config_port(prefix="")
    return make_ec2_fleet_handler(aws_client, logger, config_port)


@pytest.fixture
def instant_template(vpc_resources):
    return make_aws_template(
        subnet_id=vpc_resources["subnet_id"],
        sg_id=vpc_resources["sg_id"],
        fleet_type="instant",
    )


@pytest.fixture
def maintain_template(vpc_resources):
    return make_aws_template(
        subnet_id=vpc_resources["subnet_id"],
        sg_id=vpc_resources["sg_id"],
        fleet_type="maintain",
    )


@pytest.fixture
def request_template(vpc_resources):
    return make_aws_template(
        subnet_id=vpc_resources["subnet_id"],
        sg_id=vpc_resources["sg_id"],
        fleet_type="request",
    )


# ---------------------------------------------------------------------------
# acquire_hosts
# ---------------------------------------------------------------------------


class TestEC2FleetHandlerAcquireHosts:
    def test_acquire_instant_fleet_returns_success(self, handler, instant_template):
        """acquire_hosts with fleet_type=instant returns success and a fleet_id."""
        request = make_request(request_id="req-fleet-001", requested_count=1)
        result = handler.acquire_hosts(request, instant_template)

        assert result["success"] is True
        assert len(result["resource_ids"]) == 1
        fleet_id = result["resource_ids"][0]
        assert fleet_id.startswith("fleet-")

    def test_acquire_maintain_fleet_returns_success(self, handler, maintain_template):
        """acquire_hosts with fleet_type=maintain returns success."""
        request = make_request(request_id="req-fleet-002", requested_count=2)
        result = handler.acquire_hosts(request, maintain_template)

        assert result["success"] is True
        assert len(result["resource_ids"]) == 1

    def test_acquire_request_fleet_returns_success(self, handler, request_template):
        """acquire_hosts with fleet_type=request returns success."""
        request = make_request(request_id="req-fleet-003", requested_count=1)
        result = handler.acquire_hosts(request, request_template)

        assert result["success"] is True
        assert len(result["resource_ids"]) == 1

    def test_acquire_fleet_exists_in_aws(self, handler, instant_template, ec2):
        """The fleet created by acquire_hosts is visible via describe_fleets."""
        request = make_request(request_id="req-fleet-004", requested_count=1)
        result = handler.acquire_hosts(request, instant_template)

        fleet_id = result["resource_ids"][0]
        resp = ec2.describe_fleets(FleetIds=[fleet_id])
        fleets = resp["Fleets"]

        assert len(fleets) == 1
        assert fleets[0]["FleetId"] == fleet_id

    def test_acquire_fleet_missing_fleet_type_returns_failure(self, handler, vpc_resources):
        """acquire_hosts returns failure when fleet_type is not set."""
        bad_template = make_aws_template(
            subnet_id=vpc_resources["subnet_id"],
            sg_id=vpc_resources["sg_id"],
        )
        bad_template = bad_template.model_copy(update={"fleet_type": None})
        request = make_request(request_id="req-fleet-005")

        result = handler.acquire_hosts(request, bad_template)

        assert result["success"] is False

    def test_acquire_fleet_provider_data_contains_fleet_type(self, handler, instant_template):
        """provider_data in the result includes the fleet_type."""
        request = make_request(request_id="req-fleet-006", requested_count=1)
        result = handler.acquire_hosts(request, instant_template)

        assert result["success"] is True
        assert "provider_data" in result
        assert result["provider_data"]["resource_type"] == "ec2_fleet"


# ---------------------------------------------------------------------------
# check_hosts_status
# ---------------------------------------------------------------------------


class TestEC2FleetHandlerCheckHostsStatus:
    def test_check_hosts_status_no_resource_ids_returns_error(self, handler):
        """check_hosts_status raises when request has no resource_ids."""
        from providers.aws.exceptions.aws_exceptions import AWSInfrastructureError

        request = make_request(resource_ids=[])
        with pytest.raises(AWSInfrastructureError):
            handler.check_hosts_status(request)

    def test_check_hosts_status_after_acquire_instant(self, handler, instant_template):
        """check_hosts_status returns a list after creating an instant fleet."""
        request = make_request(request_id="req-fleet-007", requested_count=1)
        acquire_result = handler.acquire_hosts(request, instant_template)
        fleet_id = acquire_result["resource_ids"][0]

        status_request = make_request(
            resource_ids=[fleet_id],
            metadata={"fleet_type": "instant", "instance_ids": []},
        )
        result = handler.check_hosts_status(status_request)

        # moto instant fleet returns no instances — empty list is correct
        assert isinstance(result, list)

    def test_check_hosts_status_after_acquire_maintain(self, handler, maintain_template):
        """check_hosts_status returns a list after creating a maintain fleet."""
        request = make_request(request_id="req-fleet-008", requested_count=1)
        acquire_result = handler.acquire_hosts(request, maintain_template)
        fleet_id = acquire_result["resource_ids"][0]

        status_request = make_request(
            resource_ids=[fleet_id],
            metadata={"fleet_type": "maintain"},
        )
        result = handler.check_hosts_status(status_request)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# release_hosts
# ---------------------------------------------------------------------------


class TestEC2FleetHandlerReleaseHosts:
    def test_release_hosts_empty_list_is_noop(self, handler):
        """release_hosts with no machine_ids does not raise."""
        handler.release_hosts([])

    def test_release_hosts_with_resource_mapping(self, handler, instant_template):
        """release_hosts with a resource_mapping does not raise for unknown instances."""
        request = make_request(request_id="req-fleet-009", requested_count=1)
        result = handler.acquire_hosts(request, instant_template)
        fleet_id = result["resource_ids"][0]

        fake_instance_ids = ["i-bbbbbbbbbbbbbbb01"]
        resource_mapping = {iid: (fleet_id, 1) for iid in fake_instance_ids}

        try:
            handler.release_hosts(fake_instance_ids, resource_mapping=resource_mapping)
        except Exception as exc:
            assert "InvalidInstanceID" in str(exc) or "does not exist" in str(exc).lower()

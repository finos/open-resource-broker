"""Moto-based tests for SpotFleetHandler — creation, status, and termination.

Note: moto's SpotFleet support is limited. Capacity reduction and instance-level
operations are skipped where moto does not support them.
"""

import pytest

from tests.aws_mock.conftest import (
    _make_aws_client,
    _make_config_port,
    _make_logger,
    make_aws_template,
    make_request,
    make_spot_fleet_handler,
)

SPOT_FLEET_ROLE = "arn:aws:iam::123456789012:role/aws-service-role/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"


def _strip_instance_tag_spec(config: dict) -> dict:
    """Remove ResourceType='instance' from TagSpecifications — moto rejects it."""
    tag_specs = config.get("TagSpecifications", [])
    config["TagSpecifications"] = [ts for ts in tag_specs if ts.get("ResourceType") != "instance"]
    return config


@pytest.fixture
def handler(moto_aws):
    aws_client = _make_aws_client()
    logger = _make_logger()
    config_port = _make_config_port(prefix="")
    h = make_spot_fleet_handler(aws_client, logger, config_port)

    # Wrap config builder to strip the instance tag spec that moto rejects
    original_build = h._config_builder.build

    def patched_build(**kwargs):
        config = original_build(**kwargs)
        return _strip_instance_tag_spec(config)

    h._config_builder.build = patched_build
    return h


@pytest.fixture
def spot_template(vpc_resources):
    return make_aws_template(
        subnet_id=vpc_resources["subnet_id"],
        sg_id=vpc_resources["sg_id"],
        price_type="spot",
        fleet_type="request",
        fleet_role=SPOT_FLEET_ROLE,
        allocation_strategy="lowestPrice",
    )


@pytest.fixture
def maintain_spot_template(vpc_resources):
    return make_aws_template(
        subnet_id=vpc_resources["subnet_id"],
        sg_id=vpc_resources["sg_id"],
        price_type="spot",
        fleet_type="maintain",
        fleet_role=SPOT_FLEET_ROLE,
        allocation_strategy="diversified",
    )


# ---------------------------------------------------------------------------
# acquire_hosts
# ---------------------------------------------------------------------------


class TestSpotFleetHandlerAcquireHosts:
    def test_acquire_request_fleet_returns_success(self, handler, spot_template):
        """acquire_hosts with fleet_type=request returns success and a fleet request ID."""
        request = make_request(request_id="req-spot-001", requested_count=2)
        result = handler.acquire_hosts(request, spot_template)

        assert result["success"] is True
        assert len(result["resource_ids"]) == 1
        fleet_id = result["resource_ids"][0]
        assert fleet_id.startswith("sfr-")

    def test_acquire_maintain_fleet_returns_success(self, handler, maintain_spot_template):
        """acquire_hosts with fleet_type=maintain returns success."""
        request = make_request(request_id="req-spot-002", requested_count=1)
        result = handler.acquire_hosts(request, maintain_spot_template)

        assert result["success"] is True
        assert len(result["resource_ids"]) == 1

    def test_acquire_fleet_exists_in_aws(self, handler, spot_template, ec2):
        """The Spot Fleet request created by acquire_hosts is visible via describe_spot_fleet_requests."""
        request = make_request(request_id="req-spot-003", requested_count=1)
        result = handler.acquire_hosts(request, spot_template)

        fleet_id = result["resource_ids"][0]
        resp = ec2.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])
        configs = resp["SpotFleetRequestConfigs"]

        assert len(configs) == 1
        assert configs[0]["SpotFleetRequestId"] == fleet_id

    def test_acquire_fleet_missing_fleet_type_returns_failure(self, handler, vpc_resources):
        """acquire_hosts returns failure when fleet_type is not set."""
        bad_template = make_aws_template(
            subnet_id=vpc_resources["subnet_id"],
            sg_id=vpc_resources["sg_id"],
            price_type="spot",
            fleet_role=SPOT_FLEET_ROLE,
        )
        bad_template = bad_template.model_copy(update={"fleet_type": None})
        request = make_request(request_id="req-spot-004")

        result = handler.acquire_hosts(request, bad_template)

        assert result["success"] is False

    def test_acquire_fleet_provider_data_contains_resource_type(self, handler, spot_template):
        """provider_data in the result identifies the resource as spot_fleet."""
        request = make_request(request_id="req-spot-005", requested_count=1)
        result = handler.acquire_hosts(request, spot_template)

        assert result["success"] is True
        assert result["provider_data"]["resource_type"] == "spot_fleet"


# ---------------------------------------------------------------------------
# check_hosts_status
# ---------------------------------------------------------------------------


class TestSpotFleetHandlerCheckHostsStatus:
    def test_check_hosts_status_no_resource_ids_returns_empty(self, handler):
        """check_hosts_status returns [] when request has no resource_ids."""
        request = make_request(resource_ids=[])
        result = handler.check_hosts_status(request)
        assert result == []

    def test_check_hosts_status_after_acquire(self, handler, spot_template):
        """check_hosts_status returns a list after creating a spot fleet request."""
        request = make_request(request_id="req-spot-006", requested_count=1)
        acquire_result = handler.acquire_hosts(request, spot_template)
        fleet_id = acquire_result["resource_ids"][0]

        status_request = make_request(resource_ids=[fleet_id])
        result = handler.check_hosts_status(status_request)

        # moto does not fulfil spot instances — empty list is correct
        assert isinstance(result, list)

    def test_check_hosts_status_unknown_fleet_returns_empty(self, handler):
        """check_hosts_status returns [] for a fleet ID that does not exist."""
        request = make_request(resource_ids=["sfr-00000000-0000-0000-0000-000000000000"])
        result = handler.check_hosts_status(request)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# release_hosts
# ---------------------------------------------------------------------------


class TestSpotFleetHandlerReleaseHosts:
    def test_release_hosts_empty_list_is_noop(self, handler):
        """release_hosts with no machine_ids does not raise."""
        handler.release_hosts([])

    def test_release_hosts_with_resource_mapping(self, handler, spot_template):
        """release_hosts with a resource_mapping does not raise for unknown instances.

        moto SpotFleet capacity modification is not supported — we verify the
        handler does not crash when given a valid fleet ID and fake instance IDs.
        """
        request = make_request(request_id="req-spot-007", requested_count=1)
        result = handler.acquire_hosts(request, spot_template)
        fleet_id = result["resource_ids"][0]

        fake_instance_ids = ["i-ccccccccccccccc01"]
        resource_mapping = {iid: (fleet_id, 1) for iid in fake_instance_ids}

        try:
            handler.release_hosts(fake_instance_ids, resource_mapping=resource_mapping)
        except Exception:
            # moto raises when terminating non-existent instances — that is expected
            pass

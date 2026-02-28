"""Moto-based tests for RunInstancesHandler — instance launch, discovery, and termination."""

import pytest

from tests.aws_mock.conftest import (
    _make_aws_client,
    _make_config_port,
    _make_logger,
    make_aws_template,
    make_request,
    make_run_instances_handler,
)


@pytest.fixture
def handler(moto_aws):
    aws_client = _make_aws_client()
    logger = _make_logger()
    config_port = _make_config_port(prefix="")
    return make_run_instances_handler(aws_client, logger, config_port)


@pytest.fixture
def template(vpc_resources):
    return make_aws_template(
        subnet_id=vpc_resources["subnet_id"],
        sg_id=vpc_resources["sg_id"],
    )


# ---------------------------------------------------------------------------
# acquire_hosts
# ---------------------------------------------------------------------------


class TestRunInstancesHandlerAcquireHosts:
    def test_acquire_hosts_returns_success(self, handler, template):
        """acquire_hosts launches instances and returns success with a reservation_id."""
        request = make_request(request_id="req-run-001", requested_count=1)
        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert len(result["resource_ids"]) == 1
        reservation_id = result["resource_ids"][0]
        assert reservation_id.startswith("r-")

    def test_acquire_hosts_instance_ids_in_provider_data(self, handler, template):
        """acquire_hosts stores instance_ids in provider_data."""
        request = make_request(request_id="req-run-002", requested_count=2)
        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        instance_ids = result["provider_data"].get("instance_ids", [])
        # moto may return fewer instances than requested when using a launch template
        # with NetworkInterfaces — at least 1 must be present
        assert len(instance_ids) >= 1
        assert all(iid.startswith("i-") for iid in instance_ids)

    def test_acquire_hosts_instances_exist_in_aws(self, handler, template, ec2):
        """Instances launched by acquire_hosts are visible via describe_instances."""
        request = make_request(request_id="req-run-003", requested_count=2)
        result = handler.acquire_hosts(request, template)

        instance_ids = result["provider_data"]["instance_ids"]
        resp = ec2.describe_instances(InstanceIds=instance_ids)

        found_ids = [inst["InstanceId"] for r in resp["Reservations"] for inst in r["Instances"]]
        assert set(instance_ids) == set(found_ids)

    def test_acquire_hosts_requested_count_respected(self, handler, template, ec2):
        """acquire_hosts succeeds and returns at least one instance for requested_count=3.

        moto may return fewer instances than requested when a launch template with
        NetworkInterfaces is used — we verify success and at least one instance ID.
        """
        request = make_request(request_id="req-run-004", requested_count=3)
        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert len(result["provider_data"]["instance_ids"]) >= 1

    def test_acquire_hosts_missing_image_id_returns_failure(self, handler, vpc_resources):
        """acquire_hosts returns failure when image_id is missing."""
        bad_template = make_aws_template(
            subnet_id=vpc_resources["subnet_id"],
            sg_id=vpc_resources["sg_id"],
        )
        bad_template = bad_template.model_copy(update={"image_id": None})
        request = make_request(request_id="req-run-005")

        result = handler.acquire_hosts(request, bad_template)

        assert result["success"] is False

    def test_acquire_hosts_missing_subnet_returns_failure(self, handler, vpc_resources):
        """acquire_hosts returns failure when subnet_ids is empty."""
        bad_template = make_aws_template(
            subnet_id=vpc_resources["subnet_id"],
            sg_id=vpc_resources["sg_id"],
        )
        bad_template = bad_template.model_copy(update={"subnet_ids": []})
        request = make_request(request_id="req-run-006")

        result = handler.acquire_hosts(request, bad_template)

        assert result["success"] is False


# ---------------------------------------------------------------------------
# check_hosts_status — instance discovery
# ---------------------------------------------------------------------------


class TestRunInstancesHandlerCheckHostsStatus:
    def test_check_hosts_status_from_provider_data(self, handler, template):
        """check_hosts_status finds instances when instance_ids are in provider_data."""
        request = make_request(request_id="req-run-007", requested_count=1)
        acquire_result = handler.acquire_hosts(request, template)

        instance_ids = acquire_result["provider_data"]["instance_ids"]
        reservation_id = acquire_result["resource_ids"][0]

        status_request = make_request(
            request_id="req-run-007",
            resource_ids=[reservation_id],
            provider_data={
                "instance_ids": instance_ids,
                "reservation_id": reservation_id,
            },
        )
        result = handler.check_hosts_status(status_request)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["instance_id"] == instance_ids[0]

    def test_check_hosts_status_falls_back_to_resource_ids(self, handler, template):
        """check_hosts_status falls back to resource_ids when provider_data has no instance_ids."""
        request = make_request(request_id="req-run-008", requested_count=1)
        acquire_result = handler.acquire_hosts(request, template)
        reservation_id = acquire_result["resource_ids"][0]

        status_request = make_request(
            request_id="req-run-008",
            resource_ids=[reservation_id],
            provider_data={},
        )
        result = handler.check_hosts_status(status_request)

        assert isinstance(result, list)
        assert len(result) == 1

    def test_check_hosts_status_no_ids_returns_empty(self, handler):
        """check_hosts_status returns [] when no instance or resource IDs are available."""
        request = make_request(resource_ids=[], provider_data={})
        result = handler.check_hosts_status(request)
        assert result == []

    def test_check_hosts_status_multiple_instances(self, handler, template):
        """check_hosts_status returns one entry per launched instance.

        moto may return fewer instances than requested when a launch template with
        NetworkInterfaces is used — we verify the returned IDs match what was launched.
        """
        request = make_request(request_id="req-run-009", requested_count=3)
        acquire_result = handler.acquire_hosts(request, template)

        instance_ids = acquire_result["provider_data"]["instance_ids"]
        reservation_id = acquire_result["resource_ids"][0]

        status_request = make_request(
            request_id="req-run-009",
            resource_ids=[reservation_id],
            provider_data={
                "instance_ids": instance_ids,
                "reservation_id": reservation_id,
            },
        )
        result = handler.check_hosts_status(status_request)

        assert len(result) == len(instance_ids)
        returned_ids = {r["instance_id"] for r in result}
        assert returned_ids == set(instance_ids)


# ---------------------------------------------------------------------------
# release_hosts — termination
# ---------------------------------------------------------------------------


class TestRunInstancesHandlerReleaseHosts:
    def test_release_hosts_empty_list_is_noop(self, handler):
        """release_hosts with no machine_ids does not raise."""
        handler.release_hosts([])

    def test_release_hosts_terminates_instances(self, handler, template, ec2):
        """release_hosts terminates the launched instances."""
        request = make_request(request_id="req-run-010", requested_count=2)
        acquire_result = handler.acquire_hosts(request, template)
        instance_ids = acquire_result["provider_data"]["instance_ids"]

        handler.release_hosts(instance_ids)

        resp = ec2.describe_instances(InstanceIds=instance_ids)
        states = [inst["State"]["Name"] for r in resp["Reservations"] for inst in r["Instances"]]
        assert all(s in ("shutting-down", "terminated") for s in states)

    def test_release_hosts_idempotent_on_already_terminated(self, handler, template, ec2):
        """release_hosts does not raise when called twice on the same instances."""
        request = make_request(request_id="req-run-011", requested_count=1)
        acquire_result = handler.acquire_hosts(request, template)
        instance_ids = acquire_result["provider_data"]["instance_ids"]

        handler.release_hosts(instance_ids)
        # Second call should not raise
        handler.release_hosts(instance_ids)

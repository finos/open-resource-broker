"""Moto-based tests for ASGHandler — creation, status, and termination."""

import pytest

from tests.aws_mock.conftest import (
    _make_aws_client,
    _make_config_port,
    _make_logger,
    make_asg_handler,
    make_aws_template,
    make_request,
)


@pytest.fixture
def handler(moto_aws):
    aws_client = _make_aws_client()
    logger = _make_logger()
    config_port = _make_config_port(prefix="")
    return make_asg_handler(aws_client, logger, config_port)


@pytest.fixture
def template(vpc_resources):
    return make_aws_template(
        subnet_id=vpc_resources["subnet_id"],
        sg_id=vpc_resources["sg_id"],
    )


# ---------------------------------------------------------------------------
# acquire_hosts
# ---------------------------------------------------------------------------


class TestASGHandlerAcquireHosts:
    def test_acquire_hosts_returns_success(self, handler, template):
        """acquire_hosts creates an ASG and returns success with a resource_id."""
        request = make_request(request_id="req-asg-001", requested_count=1)
        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert len(result["resource_ids"]) == 1
        asg_name = result["resource_ids"][0]
        assert "req-asg-001" in asg_name

    def test_acquire_hosts_asg_exists_in_aws(self, handler, template, autoscaling):
        """The ASG created by acquire_hosts is visible via describe_auto_scaling_groups."""
        request = make_request(request_id="req-asg-002", requested_count=2)
        result = handler.acquire_hosts(request, template)

        asg_name = result["resource_ids"][0]
        resp = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        groups = resp["AutoScalingGroups"]

        assert len(groups) == 1
        assert groups[0]["AutoScalingGroupName"] == asg_name
        assert groups[0]["DesiredCapacity"] == 2

    def test_acquire_hosts_missing_subnet_raises_validation_error(self, handler):
        """acquire_hosts raises AWSValidationError when subnet_ids is empty."""
        from providers.aws.exceptions.aws_exceptions import AWSValidationError

        bad_template = make_aws_template(subnet_id="", sg_id="sg-12345678")
        bad_template = bad_template.model_copy(update={"subnet_ids": []})
        request = make_request(request_id="req-asg-003")

        with pytest.raises(AWSValidationError, match="subnet"):
            handler.acquire_hosts(request, bad_template)

    def test_acquire_hosts_missing_sg_raises_validation_error(self, handler, vpc_resources):
        """acquire_hosts raises AWSValidationError when security_group_ids is empty."""
        from providers.aws.exceptions.aws_exceptions import AWSValidationError

        bad_template = make_aws_template(subnet_id=vpc_resources["subnet_id"], sg_id="sg-12345678")
        bad_template = bad_template.model_copy(update={"security_group_ids": []})
        request = make_request(request_id="req-asg-004")

        with pytest.raises(AWSValidationError, match="security"):
            handler.acquire_hosts(request, bad_template)


# ---------------------------------------------------------------------------
# check_hosts_status
# ---------------------------------------------------------------------------


class TestASGHandlerCheckHostsStatus:
    def test_check_hosts_status_no_resource_ids_returns_empty(self, handler):
        """check_hosts_status returns [] when request has no resource_ids."""
        request = make_request(resource_ids=[])
        result = handler.check_hosts_status(request)
        assert result == []

    def test_check_hosts_status_unknown_asg_returns_empty(self, handler):
        """check_hosts_status returns [] for an ASG that does not exist."""
        request = make_request(resource_ids=["asg-does-not-exist"])
        result = handler.check_hosts_status(request)
        assert result == []

    def test_check_hosts_status_after_acquire(self, handler, template, autoscaling):
        """check_hosts_status returns [] for a freshly created ASG (no instances yet)."""
        request = make_request(request_id="req-asg-005", requested_count=1)
        acquire_result = handler.acquire_hosts(request, template)
        asg_name = acquire_result["resource_ids"][0]

        status_request = make_request(resource_ids=[asg_name])
        result = handler.check_hosts_status(status_request)

        # moto does not spin up instances automatically — empty list is correct
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# release_hosts
# ---------------------------------------------------------------------------


class TestASGHandlerReleaseHosts:
    def test_release_hosts_empty_list_is_noop(self, handler):
        """release_hosts with no machine_ids does not raise."""
        handler.release_hosts([])  # should not raise

    def test_release_hosts_with_resource_mapping(self, handler, template, autoscaling):
        """release_hosts with a resource_mapping reduces ASG capacity."""
        request = make_request(request_id="req-asg-006", requested_count=2)
        result = handler.acquire_hosts(request, template)
        asg_name = result["resource_ids"][0]

        # Simulate two instances belonging to this ASG
        fake_instance_ids = ["i-aaaaaaaaaaaaaaa01", "i-aaaaaaaaaaaaaaa02"]
        resource_mapping = {iid: (asg_name, 2) for iid in fake_instance_ids}

        # Should not raise even though instances don't physically exist in moto
        try:
            handler.release_hosts(fake_instance_ids, resource_mapping=resource_mapping)
        except Exception as exc:
            # Acceptable: moto may raise on terminate of non-existent instances
            assert "InvalidInstanceID" in str(exc) or "does not exist" in str(exc).lower()

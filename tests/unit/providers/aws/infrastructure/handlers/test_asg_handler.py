"""Unit tests for ASGHandler.check_hosts_status."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from providers.aws.infrastructure.handlers.asg_handler import ASGHandler


def _make_handler():
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    launch_template_manager = MagicMock()
    handler = ASGHandler(aws_client, logger, aws_ops, launch_template_manager)
    handler._machine_adapter = None
    return handler


def _make_request(resource_ids):
    request = MagicMock()
    request.request_id = "req-asg-123"
    request.resource_ids = resource_ids
    request.metadata = {}
    return request


def _make_client_error(code="InternalError"):
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "DescribeAutoScalingGroups")


def _formatted_instances(instance_ids, resource_id="asg-test"):
    """Return already-formatted instance dicts (as _get_asg_instances returns them)."""
    return [
        {
            "instance_id": iid,
            "resource_id": resource_id,
            "status": "running",
            "private_ip": f"10.0.2.{i}",
            "public_ip": None,
            "launch_time": None,
            "instance_type": "t3.medium",
            "image_id": "ami-789",
            "subnet_id": None,
            "security_group_ids": [],
            "vpc_id": None,
        }
        for i, iid in enumerate(instance_ids)
    ]


class TestASGHandlerCheckHostsStatus:
    def test_check_hosts_status_all_inservice(self):
        """All InService instances → returns all."""
        handler = _make_handler()
        request = _make_request(["asg-111"])
        instance_ids = ["i-asg1", "i-asg2"]

        with patch.object(
            handler,
            "_get_asg_instances",
            return_value=_formatted_instances(instance_ids, "asg-111"),
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        assert len(result) == 2
        returned_ids = {r["instance_id"] for r in result}
        assert returned_ids == set(instance_ids)

    def test_check_hosts_status_mixed_lifecycle(self):
        """_get_asg_instances extracts all instance IDs from ASG regardless of lifecycle state."""
        handler = _make_handler()
        request = _make_request(["asg-222"])

        # ASG returns InService + Terminating instances — handler returns all from _get_asg_instances
        all_ids = ["i-inservice1", "i-inservice2", "i-terminating1"]

        with patch.object(
            handler, "_get_asg_instances", return_value=_formatted_instances(all_ids, "asg-222")
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        assert len(result) == 3

    def test_check_hosts_status_asg_not_found(self):
        """_get_asg_instances returns [] when ASG not found → result is []."""
        handler = _make_handler()
        request = _make_request(["asg-missing"])

        with patch.object(handler, "_get_asg_instances", return_value=[]):
            result = handler.check_hosts_status(request)

        assert result == []

    def test_check_hosts_status_aws_error(self):
        """Exception inside per-ASG loop → logged and skipped; result is []."""
        handler = _make_handler()
        request = _make_request(["asg-err"])

        with patch.object(
            handler, "_get_asg_instances", side_effect=AWSInfrastructureError("AWS error")
        ):
            result = handler.check_hosts_status(request)

        assert result == []

    def test_check_hosts_status_no_resource_ids(self):
        """Empty resource_ids → returns [] immediately without calling AWS."""
        handler = _make_handler()
        request = _make_request([])

        with patch.object(handler, "_get_asg_instances") as mock_get:
            result = handler.check_hosts_status(request)

        assert result == []
        mock_get.assert_not_called()

    def test_check_hosts_status_returns_correct_count(self):
        """Verify count matches instances in ASG."""
        handler = _make_handler()
        request = _make_request(["asg-cnt"])
        instance_ids = ["i-cnt1", "i-cnt2", "i-cnt3"]

        with patch.object(
            handler,
            "_get_asg_instances",
            return_value=_formatted_instances(instance_ids, "asg-cnt"),
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        assert len(result) == 3

    def test_check_hosts_status_preserves_instance_ids(self):
        """Instance IDs in result match input."""
        handler = _make_handler()
        request = _make_request(["asg-ids"])
        instance_ids = ["i-asg-preserve1", "i-asg-preserve2"]

        with patch.object(
            handler,
            "_get_asg_instances",
            return_value=_formatted_instances(instance_ids, "asg-ids"),
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        returned_ids = {r["instance_id"] for r in result}
        assert returned_ids == set(instance_ids)

    def test_check_hosts_status_multiple_asgs(self):
        """Multiple ASG names in resource_ids → aggregates results from all."""
        handler = _make_handler()
        request = _make_request(["asg-A", "asg-B"])

        ids_a = ["i-a1", "i-a2"]
        ids_b = ["i-b1"]

        def get_asg_instances_side_effect(asg_name, **kwargs):
            if asg_name == "asg-A":
                return _formatted_instances(ids_a, "asg-A")
            return _formatted_instances(ids_b, "asg-B")

        with patch.object(handler, "_get_asg_instances", side_effect=get_asg_instances_side_effect):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        assert len(result) == 3
        returned_ids = {r["instance_id"] for r in result}
        assert returned_ids == {"i-a1", "i-a2", "i-b1"}

    def test_check_hosts_status_state_filtering_is_strict(self):
        """Only instances returned by _get_asg_instances appear in result."""
        handler = _make_handler()
        request = _make_request(["asg-strict"])
        # Only one instance is active
        active_ids = ["i-strict-active"]

        with patch.object(
            handler,
            "_get_asg_instances",
            return_value=_formatted_instances(active_ids, "asg-strict"),
        ):
            with patch.object(
                handler, "_format_instance_data", side_effect=lambda insts, rid, api_val: insts
            ):
                result = handler.check_hosts_status(request)

        assert len(result) == 1
        assert result[0]["instance_id"] == "i-strict-active"


class TestASGHandlerNameTag:
    def test_tag_asg_uses_config_prefix(self):
        """Name tag on ASG uses config_port prefix, not a hardcoded string."""
        handler = _make_handler()
        config_port = MagicMock()
        config_port.get_resource_prefix.return_value = "myteam-"
        handler.config_port = config_port

        aws_template = MagicMock()
        aws_template.template_id = "tmpl-1"
        aws_template.tags = {}
        handler.aws_client.autoscaling_client.create_or_update_tags = MagicMock()

        with patch.object(handler, "_retry_with_backoff") as mock_retry:
            handler._tag_asg("myteam-req-abc", aws_template, "req-abc")

        mock_retry.assert_called_once()
        call_kwargs = mock_retry.call_args[1]
        tags = call_kwargs["Tags"]
        name_tag = next(t for t in tags if t["Key"] == "Name")
        assert name_tag["Value"] == "myteam-req-abc"
        assert "hostfactory" not in name_tag["Value"]

    def test_tag_asg_empty_prefix(self):
        """Name tag with empty prefix is just the request_id."""
        handler = _make_handler()
        config_port = MagicMock()
        config_port.get_resource_prefix.return_value = ""
        handler.config_port = config_port

        aws_template = MagicMock()
        aws_template.template_id = "tmpl-2"
        aws_template.tags = {}

        with patch.object(handler, "_retry_with_backoff") as mock_retry:
            handler._tag_asg("req-xyz", aws_template, "req-xyz")

        call_kwargs = mock_retry.call_args[1]
        tags = call_kwargs["Tags"]
        name_tag = next(t for t in tags if t["Key"] == "Name")
        assert name_tag["Value"] == "req-xyz"

    def test_get_asg_instances_passes_context_to_get_instance_details(self):
        """_get_asg_instances passes request_id and resource_id to _get_instance_details."""
        handler = _make_handler()
        handler.aws_client.autoscaling_client.describe_auto_scaling_groups = MagicMock(
            return_value={
                "AutoScalingGroups": [
                    {"Instances": [{"InstanceId": "i-ctx1"}]}
                ]
            }
        )

        with patch.object(handler, "_retry_with_backoff", side_effect=lambda fn, **kw: fn(**{k: v for k, v in kw.items() if k != "operation_type"})):
            with patch.object(handler, "_get_instance_details", return_value=[]) as mock_details:
                handler._get_asg_instances("asg-ctx", request_id="req-ctx", resource_id="asg-ctx")

        mock_details.assert_called_once_with(
            ["i-ctx1"],
            request_id="req-ctx",
            resource_id="asg-ctx",
        )

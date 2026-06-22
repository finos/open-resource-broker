"""Unit tests for ASGHandler.check_hosts_status."""

from typing import Any
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from orb.domain.base.provider_fulfilment import (
    CheckHostsStatusResult,
    FulfilmentState,
    ProviderFulfilment,
)
from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler


def _make_handler() -> Any:
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    launch_template_manager = MagicMock()
    handler: Any = ASGHandler(aws_client, logger, aws_ops, launch_template_manager)
    handler._machine_adapter = None
    return handler


def _make_request(resource_ids, requested_count=2):
    request = MagicMock()
    request.request_id = "req-asg-123"
    request.resource_ids = resource_ids
    request.metadata = {}
    request.requested_count = requested_count
    return request


def _make_client_error(code="InternalError"):
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "DescribeAutoScalingGroups")


def _formatted_instances(instance_ids, resource_id="asg-test"):
    """Return already-formatted instance dicts."""
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


def _asg_result(instance_ids, resource_id="asg-test", state: FulfilmentState = "fulfilled"):
    """Build a CheckHostsStatusResult for mocking _get_asg_status."""
    return CheckHostsStatusResult(
        instances=_formatted_instances(instance_ids, resource_id),
        fulfilment=ProviderFulfilment(state=state, message="test"),
    )


class TestASGHandlerCheckHostsStatus:
    def test_check_hosts_status_all_inservice(self):
        """All InService instances → CheckHostsStatusResult with all instances and fulfilled state."""
        handler = _make_handler()
        request = _make_request(["asg-111"])
        instance_ids = ["i-asg1", "i-asg2"]

        with patch.object(
            handler,
            "_get_asg_status",
            return_value=_asg_result(instance_ids, "asg-111", state="fulfilled"),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert len(result.instances) == 2
        returned_ids = {r["instance_id"] for r in result.instances}
        assert returned_ids == set(instance_ids)
        assert result.fulfilment.state == "fulfilled"

    def test_check_hosts_status_mixed_lifecycle(self):
        """ASG with mixed lifecycle states — handler reports what _get_asg_status returns."""
        handler = _make_handler()
        request = _make_request(["asg-222"])

        all_ids = ["i-inservice1", "i-inservice2", "i-terminating1"]

        with patch.object(
            handler,
            "_get_asg_status",
            return_value=_asg_result(all_ids, "asg-222", state="in_progress"),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert len(result.instances) == 3

    def test_check_hosts_status_asg_not_found(self):
        """_get_asg_status raises → exception logged and skipped; result has empty instances and in_progress."""
        handler = _make_handler()
        request = _make_request(["asg-missing"])

        with patch.object(
            handler, "_get_asg_status", side_effect=AWSInfrastructureError("ASG not found")
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert result.instances == []
        assert result.fulfilment.state == "in_progress"

    def test_check_hosts_status_aws_error(self):
        """Exception inside per-ASG loop → logged and skipped; empty instances, in_progress."""
        handler = _make_handler()
        request = _make_request(["asg-err"])

        with patch.object(
            handler, "_get_asg_status", side_effect=AWSInfrastructureError("AWS error")
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert result.instances == []
        assert result.fulfilment.state == "in_progress"

    def test_check_hosts_status_no_resource_ids(self):
        """Empty resource_ids → CheckHostsStatusResult with empty instances and in_progress, no AWS calls."""
        handler = _make_handler()
        request = _make_request([])

        with patch.object(handler, "_get_asg_status") as mock_get:
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert result.instances == []
        assert result.fulfilment.state == "in_progress"
        mock_get.assert_not_called()

    def test_check_hosts_status_returns_correct_count(self):
        """Verify instance count in result.instances matches instances returned by _get_asg_status."""
        handler = _make_handler()
        request = _make_request(["asg-cnt"])
        instance_ids = ["i-cnt1", "i-cnt2", "i-cnt3"]

        with patch.object(
            handler,
            "_get_asg_status",
            return_value=_asg_result(instance_ids, "asg-cnt", state="fulfilled"),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert len(result.instances) == 3
        assert result.fulfilment.state == "fulfilled"

    def test_check_hosts_status_preserves_instance_ids(self):
        """Instance IDs in result.instances match input."""
        handler = _make_handler()
        request = _make_request(["asg-ids"])
        instance_ids = ["i-asg-preserve1", "i-asg-preserve2"]

        with patch.object(
            handler,
            "_get_asg_status",
            return_value=_asg_result(instance_ids, "asg-ids", state="fulfilled"),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert isinstance(result.fulfilment, ProviderFulfilment)
        returned_ids = {r["instance_id"] for r in result.instances}
        assert returned_ids == set(instance_ids)
        assert result.fulfilment.state == "fulfilled"

    def test_check_hosts_status_multiple_asgs(self):
        """Multiple ASG names in resource_ids → aggregates instances from all ASGs."""
        handler = _make_handler()
        request = _make_request(["asg-A", "asg-B"])

        ids_a = ["i-a1", "i-a2"]
        ids_b = ["i-b1"]

        def get_asg_status_side_effect(asg_name, **kwargs):
            if asg_name == "asg-A":
                return _asg_result(ids_a, "asg-A", state="fulfilled")
            return _asg_result(ids_b, "asg-B", state="fulfilled")

        with patch.object(handler, "_get_asg_status", side_effect=get_asg_status_side_effect):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert len(result.instances) == 3
        returned_ids = {r["instance_id"] for r in result.instances}
        assert returned_ids == {"i-a1", "i-a2", "i-b1"}
        assert result.fulfilment.state == "fulfilled"

    def test_check_hosts_status_state_filtering_is_strict(self):
        """Only instances returned by _get_asg_status appear in result.instances."""
        handler = _make_handler()
        request = _make_request(["asg-strict"])
        active_ids = ["i-strict-active"]

        with patch.object(
            handler,
            "_get_asg_status",
            return_value=_asg_result(active_ids, "asg-strict", state="fulfilled"),
        ):
            result = handler.check_hosts_status(request)

        assert isinstance(result, CheckHostsStatusResult)
        assert isinstance(result.fulfilment, ProviderFulfilment)
        assert len(result.instances) == 1
        assert result.instances[0]["instance_id"] == "i-strict-active"
        assert result.fulfilment.state == "fulfilled"
        assert result.fulfilment.target_units is None


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
            return_value={"AutoScalingGroups": [{"Instances": [{"InstanceId": "i-ctx1"}]}]}
        )

        with patch.object(
            handler,
            "_retry_with_backoff",
            side_effect=lambda fn, **kw: fn(
                **{k: v for k, v in kw.items() if k != "operation_type"}
            ),
        ):
            with patch.object(handler, "_get_instance_details", return_value=[]) as mock_details:
                handler._get_asg_instances("asg-ctx", request_id="req-ctx", resource_id="asg-ctx")

        mock_details.assert_called_once_with(
            ["i-ctx1"],
            request_id="req-ctx",
            resource_id="asg-ctx",
            provider_api="ASG",
        )

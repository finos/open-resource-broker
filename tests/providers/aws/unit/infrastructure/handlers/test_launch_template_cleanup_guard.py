"""Launch template cleanup must wait for the last live instance of a request."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from orb.config.schemas.cleanup_schema import CleanupConfig
from orb.providers.aws.infrastructure.handlers.launch_template_cleanup import (
    LaunchTemplateCleanupService,
)


def _service(live_response=None, live_error=None):
    aws_client = MagicMock()
    ec2 = aws_client.ec2_client
    if live_error is not None:
        ec2.describe_instances.side_effect = live_error
    else:
        ec2.describe_instances.return_value = live_response or {"Reservations": []}
    ec2.describe_launch_templates.return_value = {
        "LaunchTemplates": [
            {
                "LaunchTemplateId": "lt-1",
                "LaunchTemplateName": "orb-lt",
                "Tags": [{"Key": "orb:managed-by", "Value": "open-resource-broker"}],
            }
        ]
    }
    svc = LaunchTemplateCleanupService(aws_client, MagicMock(), MagicMock())
    svc.get_cleanup_config = CleanupConfig  # type: ignore[method-assign,assignment]
    return svc, ec2


@pytest.mark.unit
class TestLaunchTemplateCleanupGuard:
    def test_partial_return_keeps_template_while_instances_remain(self):
        svc, ec2 = _service({"Reservations": [{"Instances": [{"InstanceId": "i-2"}]}]})

        svc.cleanup_on_zero_capacity("run_instances", "req-1")

        ec2.delete_launch_template.assert_not_called()
        filters = ec2.describe_instances.call_args.kwargs["Filters"]
        assert {"Name": "tag:orb:request-id", "Values": ["req-1"]} in filters
        states = next(f for f in filters if f["Name"] == "instance-state-name")["Values"]
        assert "shutting-down" not in states
        assert "terminated" not in states

    def test_final_return_deletes_template(self):
        svc, ec2 = _service({"Reservations": []})

        svc.cleanup_on_zero_capacity("run_instances", "req-1")

        ec2.delete_launch_template.assert_called_once_with(LaunchTemplateId="lt-1")

    def test_cleanup_completes_on_the_later_return(self):
        svc, ec2 = _service()
        ec2.describe_instances.side_effect = [
            {"Reservations": [{"Instances": [{"InstanceId": "i-2"}]}]},
            {"Reservations": []},
            {"Reservations": []},
        ]

        svc.cleanup_on_zero_capacity("run_instances", "req-1")
        ec2.delete_launch_template.assert_not_called()
        svc.cleanup_on_zero_capacity("run_instances", "req-1")
        ec2.delete_launch_template.assert_called_once()

    def test_lookup_failure_assumes_instances_remain(self):
        svc, ec2 = _service(
            live_error=ClientError(
                {"Error": {"Code": "RequestLimitExceeded", "Message": "m"}}, "op"
            )
        )

        svc.cleanup_on_zero_capacity("run_instances", "req-1")

        ec2.delete_launch_template.assert_not_called()
        ec2.describe_launch_templates.assert_not_called()

    def test_just_terminated_instances_are_ignored(self):
        svc, ec2 = _service(
            {"Reservations": [{"Instances": [{"InstanceId": "i-1"}, {"InstanceId": "i-2"}]}]}
        )

        svc.cleanup_on_zero_capacity("run_instances", "req-1", ["i-1", "i-2"])

        ec2.delete_launch_template.assert_called_once_with(LaunchTemplateId="lt-1")

    def test_instance_outside_the_excluded_set_keeps_template(self):
        svc, ec2 = _service(
            {"Reservations": [{"Instances": [{"InstanceId": "i-1"}, {"InstanceId": "i-3"}]}]}
        )

        svc.cleanup_on_zero_capacity("run_instances", "req-1", ["i-1"])

        ec2.delete_launch_template.assert_not_called()

    def test_later_result_pages_are_followed(self):
        svc, ec2 = _service()
        ec2.describe_instances.side_effect = [
            {"Reservations": [], "NextToken": "page-2"},
            {"Reservations": [{"Instances": [{"InstanceId": "i-9"}]}]},
        ]

        svc.cleanup_on_zero_capacity("run_instances", "req-1")

        assert ec2.describe_instances.call_args_list[1].kwargs["NextToken"] == "page-2"
        ec2.delete_launch_template.assert_not_called()


@pytest.mark.unit
class TestLaunchTemplateIdGuard:
    """Instances launched from the template keep it, even without ORB's request tag."""

    @staticmethod
    def _by_filter(live_filter: str, instances: list[dict]):
        def describe(**kwargs):
            names = {f["Name"] for f in kwargs["Filters"]}
            if live_filter in names:
                return {"Reservations": [{"Instances": instances}]}
            return {"Reservations": []}

        return describe

    def test_instance_with_only_the_aws_template_tag_keeps_the_template(self):
        svc, ec2 = _service()
        ec2.describe_instances.side_effect = self._by_filter(
            "tag:aws:ec2launchtemplate:id", [{"InstanceId": "i-custom"}]
        )

        svc.cleanup_on_zero_capacity("run_instances", "req-1")

        ec2.delete_launch_template.assert_not_called()
        queried = [
            f["Values"]
            for c in ec2.describe_instances.call_args_list
            for f in c.kwargs["Filters"]
            if f["Name"] == "tag:aws:ec2launchtemplate:id"
        ]
        assert queried == [["lt-1"]]

    def test_just_terminated_instances_do_not_keep_the_template(self):
        svc, ec2 = _service()
        ec2.describe_instances.side_effect = self._by_filter(
            "tag:aws:ec2launchtemplate:id", [{"InstanceId": "i-gone"}]
        )

        svc.cleanup_on_zero_capacity("run_instances", "req-1", ["i-gone"])

        ec2.delete_launch_template.assert_called_once_with(LaunchTemplateId="lt-1")

    def test_template_lookup_failure_keeps_the_template(self):
        svc, ec2 = _service()

        def describe(**kwargs):
            if any(f["Name"] == "tag:aws:ec2launchtemplate:id" for f in kwargs["Filters"]):
                raise RuntimeError("throttled")
            return {"Reservations": []}

        ec2.describe_instances.side_effect = describe

        svc.cleanup_on_zero_capacity("run_instances", "req-1")

        ec2.delete_launch_template.assert_not_called()


@pytest.mark.unit
class TestInstancesCarryRequestTag:
    """The guard finds fleet instances through the tag the launch template applies."""

    def test_default_launch_template_spec_tags_instances_with_request_id(self):
        from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer
        from orb.providers.aws.infrastructure.services.aws_native_spec_service import (
            AWSNativeSpecService,
        )

        native = MagicMock()
        native.spec_renderer = JinjaSpecRenderer(MagicMock())
        service = AWSNativeSpecService(native, MagicMock())

        spec = service.render_default_spec(
            "launch-template",
            {
                "image_id": "ami-1",
                "instance_type": "t3.micro",
                "instance_name": "n",
                "request_id": "req-42",
                "template_id": "tpl",
                "created_by": "orb",
                "has_custom_tags": False,
                "custom_tags": [],
            },
        )

        tags = {t["Key"]: t["Value"] for t in spec["TagSpecifications"][0]["Tags"]}
        assert spec["TagSpecifications"][0]["ResourceType"] == "instance"
        assert tags["orb:request-id"] == "req-42"

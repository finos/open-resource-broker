"""An ASG create retried after a lost response must not orphan the group it created."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler

pytestmark = pytest.mark.unit


def _error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": f"{code} text"}}, "Op")


def _real_retry_handler(handler_cls):
    aws_client = MagicMock()
    aws_client.get_selected_aws_provider_config.return_value = None
    config_port = MagicMock()
    config_port.app_config = None
    config_port.get_resource_prefix.return_value = ""
    return handler_cls(aws_client, MagicMock(), MagicMock(), MagicMock(), config_port=config_port)


class TestAsgCreateAfterALostResponse:
    """A retry that finds the group its first attempt created must not orphan it."""

    @staticmethod
    def _handler():
        handler = _real_retry_handler(ASGHandler)
        handler.launch_template_manager.create_or_update_launch_template.return_value = MagicMock(
            template_id="lt-1", version="1"
        )
        handler._config_builder = MagicMock()
        handler._config_builder.build.return_value = {"AutoScalingGroupName": "req-1"}
        handler._tag_asg = MagicMock()  # type: ignore[method-assign]
        handler.config_port.get_resource_prefix.return_value = ""
        return handler

    @staticmethod
    def _template():
        return SimpleNamespace(instance_protection=None, lifecycle_hooks=None)

    @staticmethod
    def _request():
        request = MagicMock()
        request.request_id = "req-1"
        return request

    def test_the_groups_own_existing_group_counts_as_created(self):
        handler = self._handler()
        asg = handler.aws_client.autoscaling_client
        asg.create_auto_scaling_group.side_effect = _error("AlreadyExists")
        asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {
                    "Tags": [{"Key": "orb:request-id", "Value": "req-1"}],
                    "AutoScalingGroupName": "req-1",
                }
            ]
        }

        assert handler._create_asg_internal(self._request(), self._template()) == "req-1"
        assert asg.create_auto_scaling_group.call_count == 1  # AlreadyExists is not retried

    def test_a_group_with_the_same_launch_template_counts_as_ours(self):
        handler = self._handler()
        asg = handler.aws_client.autoscaling_client
        asg.create_auto_scaling_group.side_effect = _error("AlreadyExists")
        asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"LaunchTemplate": {"LaunchTemplateId": "lt-1"}}]
        }

        assert handler._create_asg_internal(self._request(), self._template()) == "req-1"

    def test_someone_elses_group_of_that_name_is_a_real_conflict(self):
        handler = self._handler()
        asg = handler.aws_client.autoscaling_client
        asg.create_auto_scaling_group.side_effect = _error("AlreadyExists")
        asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {
                    "Tags": [{"Key": "orb:request-id", "Value": "req-other"}],
                    "LaunchTemplate": {"LaunchTemplateId": "lt-other"},
                }
            ]
        }

        with pytest.raises(Exception, match="AlreadyExists|exists|AWS Error"):
            handler._create_asg_internal(self._request(), self._template())


class TestCleanupReceivesTheRealRequestId:
    @pytest.mark.parametrize("prefix", ["", "orb-", "other-prefix-"])
    def test_the_request_id_is_recovered_whatever_prefix_the_group_was_created_with(self, prefix):
        handler = _real_retry_handler(ASGHandler)
        handler.config_port.get_resource_prefix.return_value = "orb-"
        request_id = "req-0a1b2c3d-1111-2222-3333-444455556666"

        assert handler._request_id_for_asg(f"{prefix}{request_id}") == request_id

    def test_a_name_without_a_request_id_falls_back_to_the_prefix_rule(self):
        handler = _real_retry_handler(ASGHandler)
        handler.config_port.get_resource_prefix.return_value = "orb-"
        assert handler._request_id_for_asg("orb-custom") == "custom"

    def test_the_capacity_manager_cleanup_gets_the_request_id_not_the_group_name(self):
        handler = _real_retry_handler(ASGHandler)
        handler.config_port.get_resource_prefix.return_value = "orb-"
        handler._cleanup_on_zero_capacity = MagicMock()  # type: ignore[method-assign]
        request_id = "req-0a1b2c3d-1111-2222-3333-444455556666"

        handler._cleanup_asg_on_zero_capacity("asg", f"orb-{request_id}", ["i-1"])

        handler._cleanup_on_zero_capacity.assert_called_once_with("asg", request_id, ["i-1"])

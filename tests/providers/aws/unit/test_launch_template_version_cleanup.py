"""ORB-created versions on user-provided launch templates are removed after the return.

A user's template and its own versions are never touched: a version is deleted
only when its description is exactly the one ORB wrote for the request.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from orb.config.schemas.cleanup_schema import CleanupConfig
from orb.config.schemas.provider_strategy_schema import ProviderDefaults
from orb.providers.aws.infrastructure.handlers.launch_template_cleanup import (
    LaunchTemplateCleanupService,
)

pytestmark = pytest.mark.unit

REQUEST = "req-aaaa"
LT = "lt-user"


def _service(**cleanup_overrides) -> LaunchTemplateCleanupService:
    cleanup = CleanupConfig(enabled=True, delete_launch_template=True, dry_run=False)
    cleanup = cleanup.model_copy(update=cleanup_overrides)
    config_port = MagicMock()
    provider_config = MagicMock()
    provider_config.provider_defaults = {"aws": ProviderDefaults(cleanup=cleanup.model_dump())}
    config_port.get_provider_config.return_value = provider_config
    aws_client = MagicMock()
    ec2 = aws_client.ec2_client
    # The released instance was started from version 7 of the user's template.
    ec2.describe_instances.side_effect = lambda **kw: (
        {"Reservations": []}  # no live instances for the request
        if "Filters" in kw
        else {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-1",
                            "Tags": [
                                {"Key": "aws:ec2launchtemplate:id", "Value": LT},
                                {"Key": "aws:ec2launchtemplate:version", "Value": "7"},
                            ],
                        }
                    ]
                }
            ]
        }
    )
    ec2.describe_launch_templates.return_value = {"LaunchTemplates": [{"LaunchTemplateId": LT}]}
    ec2.describe_launch_template_versions.return_value = {
        "LaunchTemplateVersions": [
            {
                "VersionNumber": 7,
                "DefaultVersion": False,
                "VersionDescription": f"Override for request {REQUEST}",
            }
        ]
    }
    ec2.delete_launch_template_versions.return_value = {
        "UnsuccessfullyDeletedLaunchTemplateVersions": []
    }
    return LaunchTemplateCleanupService(aws_client, config_port, MagicMock())


def _ec2(service: LaunchTemplateCleanupService) -> MagicMock:
    return service._aws_client.ec2_client


def test_the_orb_version_of_a_user_template_is_deleted_once_its_instances_are_gone():
    service = _service()

    service.cleanup_on_zero_capacity("ec2_fleet", REQUEST, ["i-1"])

    _ec2(service).delete_launch_template_versions.assert_called_once_with(
        LaunchTemplateId=LT, Versions=["7"]
    )
    _ec2(service).delete_launch_template.assert_not_called()


def test_a_version_the_user_created_is_never_deleted():
    service = _service()
    _ec2(service).describe_launch_template_versions.return_value = {
        "LaunchTemplateVersions": [
            {"VersionNumber": 7, "DefaultVersion": False, "VersionDescription": "my tuning"}
        ]
    }

    service.delete_orb_template_versions(REQUEST, ["i-1"])

    _ec2(service).delete_launch_template_versions.assert_not_called()


def test_a_version_written_for_another_request_is_never_deleted():
    service = _service()
    _ec2(service).describe_launch_template_versions.return_value = {
        "LaunchTemplateVersions": [
            {
                "VersionNumber": 7,
                "DefaultVersion": False,
                "VersionDescription": "Override for request req-other",
            }
        ]
    }

    service.delete_orb_template_versions(REQUEST, ["i-1"])

    _ec2(service).delete_launch_template_versions.assert_not_called()


def test_the_default_version_is_never_deleted():
    service = _service()
    _ec2(service).describe_launch_template_versions.return_value = {
        "LaunchTemplateVersions": [
            {
                "VersionNumber": 7,
                "DefaultVersion": True,
                "VersionDescription": f"Override for request {REQUEST}",
            }
        ]
    }

    service.delete_orb_template_versions(REQUEST, ["i-1"])

    _ec2(service).delete_launch_template_versions.assert_not_called()


def test_templates_orb_manages_are_left_to_the_whole_template_cleanup():
    service = _service()
    _ec2(service).describe_launch_templates.return_value = {
        "LaunchTemplates": [
            {
                "LaunchTemplateId": LT,
                "Tags": [{"Key": "orb:managed-by", "Value": "open-resource-broker"}],
            }
        ]
    }

    service.delete_orb_template_versions(REQUEST, ["i-1"])

    _ec2(service).delete_launch_template_versions.assert_not_called()


def test_nothing_is_deleted_while_the_request_still_has_live_instances():
    service = _service()
    _ec2(service).describe_instances.side_effect = lambda **kw: (
        {"Reservations": [{"Instances": [{"InstanceId": "i-2"}]}]} if "Filters" in kw else {}
    )

    service.delete_orb_template_versions(REQUEST, ["i-1"])

    _ec2(service).delete_launch_template_versions.assert_not_called()


def test_dry_run_and_disabled_cleanup_delete_nothing():
    for overrides in ({"dry_run": True}, {"enabled": False}, {"delete_launch_template": False}):
        service = _service(**overrides)
        service.delete_orb_template_versions(REQUEST, ["i-1"])
        _ec2(service).delete_launch_template_versions.assert_not_called()


def test_without_released_instances_there_is_nothing_to_look_up():
    service = _service()

    service.delete_orb_template_versions(REQUEST, [])

    _ec2(service).describe_launch_template_versions.assert_not_called()


def test_a_lookup_failure_only_warns():
    service = _service()
    _ec2(service).describe_launch_template_versions.side_effect = ClientError(
        {"Error": {"Code": "UnauthorizedOperation", "Message": "denied"}}, "Describe"
    )

    service.delete_orb_template_versions(REQUEST, ["i-1"])

    _ec2(service).delete_launch_template_versions.assert_not_called()
    service._logger.warning.assert_called()


def test_a_failed_deletion_only_warns_and_a_partial_one_is_reported():
    service = _service()
    _ec2(service).delete_launch_template_versions.return_value = {
        "UnsuccessfullyDeletedLaunchTemplateVersions": [
            {"VersionNumber": 7, "ResponseError": {"Message": "in use"}}
        ]
    }
    service.delete_orb_template_versions(REQUEST, ["i-1"])
    assert any("in use" in str(c) for c in service._logger.warning.call_args_list)

    denied = _service()
    _ec2(denied).delete_launch_template_versions.side_effect = ClientError(
        {"Error": {"Code": "UnauthorizedOperation", "Message": "denied"}}, "Delete"
    )
    denied.delete_orb_template_versions(REQUEST, ["i-1"])
    denied._logger.warning.assert_called()

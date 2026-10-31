"""Version cleanup against moto: the version is found from the released instance's tags.

moto does not implement DeleteLaunchTemplateVersions, so the delete call is
recorded instead; everything before it runs against moto.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import boto3
import pytest

from orb.providers.aws.infrastructure.handlers.launch_template_cleanup import (
    LaunchTemplateCleanupService,
)
from tests.providers.aws.mocked.test_cleanup_e2e import _make_cleanup_config_port, _make_logger

REGION = "eu-west-2"

pytestmark = [pytest.mark.moto]


def test_the_orb_version_is_found_from_the_released_instance_and_the_users_is_kept(
    moto_aws, moto_vpc_resources
):
    ec2 = boto3.client("ec2", region_name=REGION)
    user_lt = ec2.create_launch_template(
        LaunchTemplateName="user-template",
        LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.micro"},
    )["LaunchTemplate"]["LaunchTemplateId"]
    ec2.create_launch_template_version(
        LaunchTemplateId=user_lt,
        VersionDescription="my own tuning",
        LaunchTemplateData={"InstanceType": "t3.small"},
    )
    orb_version = ec2.create_launch_template_version(
        LaunchTemplateId=user_lt,
        VersionDescription="Override for request req-moto-1",
        LaunchTemplateData={"InstanceType": "t3.medium"},
    )["LaunchTemplateVersion"]["VersionNumber"]
    instance = ec2.run_instances(
        MinCount=1,
        MaxCount=1,
        SubnetId=moto_vpc_resources["subnet_ids"][0],
        LaunchTemplate={"LaunchTemplateId": user_lt, "Version": str(orb_version)},
        TagSpecifications=[
            {"ResourceType": "instance", "Tags": [{"Key": "orb:request-id", "Value": "req-moto-1"}]}
        ],
    )["Instances"][0]["InstanceId"]
    ec2.terminate_instances(InstanceIds=[instance])

    aws_client = MagicMock()
    aws_client.ec2_client = MagicMock(wraps=ec2)
    aws_client.ec2_client.delete_launch_template_versions = MagicMock(
        return_value={"UnsuccessfullyDeletedLaunchTemplateVersions": []}
    )
    service = LaunchTemplateCleanupService(aws_client, _make_cleanup_config_port(), _make_logger())

    service.cleanup_on_zero_capacity("ec2_fleet", "req-moto-1", [instance])

    aws_client.ec2_client.delete_launch_template_versions.assert_called_once_with(
        LaunchTemplateId=user_lt, Versions=[str(orb_version)]
    )
    # The user's template itself is never deleted.
    assert ec2.describe_launch_templates(LaunchTemplateIds=[user_lt])["LaunchTemplates"]

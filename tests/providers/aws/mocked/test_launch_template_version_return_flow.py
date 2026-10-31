"""The version ORB adds to a user's launch template is removed by the real return path.

Goes through the EC2 Fleet handler: acquire on a user-provided launch template with
overrides, then ``release_hosts`` for the instances (what ``orb machines return`` ends
in). moto neither stamps the launch template tags on instances nor implements
DeleteLaunchTemplateVersions, so the tags are stamped here and the delete is recorded.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from orb.providers.aws.utilities.aws_operations import AWSOperations
from tests.providers.aws.mocked.test_cleanup_e2e import (
    _make_aws_client,
    _make_cleanup_config_port,
    _make_logger,
    _make_request,
)

REGION = "eu-west-2"
pytestmark = [pytest.mark.moto]


@pytest.fixture
def setup(moto_aws, moto_vpc_resources):
    ec2 = boto3.client("ec2", region_name=REGION)
    user_lt = ec2.create_launch_template(
        LaunchTemplateName="user-template",
        LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.micro"},
    )["LaunchTemplate"]["LaunchTemplateId"]

    aws_client = _make_aws_client()
    recorded = MagicMock(wraps=ec2)
    recorded.delete_launch_template_versions = MagicMock(
        return_value={"UnsuccessfullyDeletedLaunchTemplateVersions": []}
    )
    aws_client.ec2_client = recorded
    logger = _make_logger()
    config_port = _make_cleanup_config_port()
    handler = EC2FleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=AWSOperations(aws_client, logger, config_port),
        launch_template_manager=AWSLaunchTemplateManager(aws_client, logger, config_port),
        config_port=config_port,
    )
    template = AWSTemplate(
        template_id="tpl-user-lt",
        name="user-lt",
        provider_api="EC2Fleet",
        machine_types={"t3.micro": 1},
        max_instances=5,
        price_type="ondemand",
        fleet_type="instant",
        launch_template_id=user_lt,
        machine_image="ami-87654321",  # an override: ORB adds a version to the user's template
        subnet_ids=[moto_vpc_resources["subnet_ids"][0]],
        security_group_ids=[moto_vpc_resources["sg_id"]],
    )
    return ec2, recorded, handler, template, user_lt


def _acquire(handler, template, request_id) -> tuple[str, list[str]]:
    result: Any = handler.acquire_hosts(_make_request(request_id=request_id), template)
    assert result["success"] is True
    ids = [i.get("instance_id") or i.get("InstanceId") for i in result["instances"]] or result.get(
        "instance_ids", []
    )
    assert ids, result
    return result["resource_ids"][0], ids


def test_returning_the_instances_deletes_the_version_orb_added(setup):
    ec2, recorded, handler, template, user_lt = setup
    request_id = "req-aaaaaaaa-1111-2222-3333-444444444444"
    fleet_id, instance_ids = _acquire(handler, template, request_id)

    versions = ec2.describe_launch_template_versions(LaunchTemplateId=user_lt)[
        "LaunchTemplateVersions"
    ]
    orb_version = next(
        v["VersionNumber"]
        for v in versions
        if v.get("VersionDescription") == f"Override for request {request_id}"
    )
    # EC2 stamps these on instances; moto does not.
    ec2.create_tags(
        Resources=instance_ids,
        Tags=[
            {"Key": "aws:ec2launchtemplate:id", "Value": user_lt},
            {"Key": "aws:ec2launchtemplate:version", "Value": str(orb_version)},
            {"Key": "orb:request-id", "Value": request_id},
        ],
    )

    handler.release_hosts(
        instance_ids,
        resource_mapping={iid: (fleet_id, 1) for iid in instance_ids},
        request_id=request_id,
    )

    recorded.delete_launch_template_versions.assert_called_once_with(
        LaunchTemplateId=user_lt, Versions=[str(orb_version)]
    )
    assert ec2.describe_launch_templates(LaunchTemplateIds=[user_lt])["LaunchTemplates"]

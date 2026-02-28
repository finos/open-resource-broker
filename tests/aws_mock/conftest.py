"""Shared fixtures for moto-based AWS mock tests."""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from providers.aws.utilities.aws_operations import AWSOperations

# ---------------------------------------------------------------------------
# Core moto context — all tests in this package run inside mock_aws
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def aws_credentials(monkeypatch):
    """Ensure boto3 never hits real AWS."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture(scope="function")
def moto_aws(aws_credentials):
    """Start moto mock_aws context for the duration of each test."""
    with mock_aws():
        yield


@pytest.fixture
def ec2(moto_aws):
    return boto3.client("ec2", region_name="us-east-1")


@pytest.fixture
def autoscaling(moto_aws):
    return boto3.client("autoscaling", region_name="us-east-1")


# ---------------------------------------------------------------------------
# VPC / subnet / SG resources
# ---------------------------------------------------------------------------


@pytest.fixture
def vpc_resources(ec2):
    """Create a minimal VPC, subnet, and security group in moto."""
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]

    subnet = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone="us-east-1a")
    subnet_id = subnet["Subnet"]["SubnetId"]

    sg = ec2.create_security_group(GroupName="test-sg", Description="test", VpcId=vpc_id)
    sg_id = sg["GroupId"]

    return {"vpc_id": vpc_id, "subnet_id": subnet_id, "sg_id": sg_id}


# ---------------------------------------------------------------------------
# Handler factory helpers
# ---------------------------------------------------------------------------


def _make_logger():
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_config_port(prefix: str = ""):
    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = prefix
    config_port.get_cleanup_config.return_value = {"enabled": False}
    return config_port


def _make_aws_client(region: str = "us-east-1") -> AWSClient:
    """Build a real AWSClient backed by moto (must be called inside mock_aws context)."""
    import boto3 as _boto3

    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = _boto3.client("ec2", region_name=region)
    aws_client.autoscaling_client = _boto3.client("autoscaling", region_name=region)
    aws_client.sts_client = _boto3.client("sts", region_name=region)
    return aws_client


def _make_launch_template_manager(aws_client: AWSClient, logger) -> AWSLaunchTemplateManager:
    """Build a launch template manager that uses the moto-backed ec2 client."""
    lt_manager = MagicMock(spec=AWSLaunchTemplateManager)

    def _create_or_update(template, request):
        from providers.aws.infrastructure.launch_template.manager import LaunchTemplateResult

        # Create a real launch template in moto
        lt_name = f"orb-lt-{request.request_id}"
        try:
            resp = aws_client.ec2_client.create_launch_template(
                LaunchTemplateName=lt_name,
                LaunchTemplateData={
                    "ImageId": template.image_id or "ami-12345678",
                    "InstanceType": (
                        next(iter(template.machine_types.keys()))
                        if template.machine_types
                        else "t3.medium"
                    ),
                    "NetworkInterfaces": [
                        {
                            "DeviceIndex": 0,
                            "SubnetId": template.subnet_ids[0] if template.subnet_ids else "",
                            "Groups": template.security_group_ids or [],
                            "AssociatePublicIpAddress": False,
                        }
                    ],
                },
            )
            lt_id = resp["LaunchTemplate"]["LaunchTemplateId"]
            version = str(resp["LaunchTemplate"]["LatestVersionNumber"])
        except Exception:
            lt_id = "lt-mock"
            version = "1"
        return LaunchTemplateResult(
            template_id=lt_id,
            version=version,
            template_name=lt_name,
            is_new_template=True,
        )

    lt_manager.create_or_update_launch_template.side_effect = _create_or_update
    return lt_manager


def make_asg_handler(aws_client, logger, config_port) -> ASGHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_launch_template_manager(aws_client, logger)
    handler = ASGHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    return handler


def make_ec2_fleet_handler(aws_client, logger, config_port) -> EC2FleetHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_launch_template_manager(aws_client, logger)
    handler = EC2FleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    return handler


def make_spot_fleet_handler(aws_client, logger, config_port) -> SpotFleetHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_launch_template_manager(aws_client, logger)
    handler = SpotFleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    return handler


def make_run_instances_handler(aws_client, logger, config_port) -> RunInstancesHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_launch_template_manager(aws_client, logger)
    handler = RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    return handler


# ---------------------------------------------------------------------------
# Shared request / template factories
# ---------------------------------------------------------------------------


def make_request(
    request_id: str = "req-test-001",
    requested_count: int = 2,
    template_id: str = "tpl-test",
    metadata: dict | None = None,
    resource_ids: list[str] | None = None,
    provider_data: dict | None = None,
) -> Any:
    request = MagicMock()
    request.request_id = request_id
    request.requested_count = requested_count
    request.template_id = template_id
    request.metadata = metadata or {}
    request.resource_ids = resource_ids or []
    request.provider_data = provider_data or {}
    request.provider_api = None
    return request


def make_aws_template(
    subnet_id: str,
    sg_id: str,
    instance_type: str = "t3.micro",
    image_id: str = "ami-12345678",
    price_type: str = "ondemand",
    fleet_type: str | None = None,
    fleet_role: str | None = None,
    allocation_strategy: str | None = None,
) -> AWSTemplate:
    kwargs: dict[str, Any] = dict(
        template_id="tpl-test",
        name="test-template",
        provider_api="ASG",
        instance_type=instance_type,
        machine_types={instance_type: 1},
        image_id=image_id,
        max_instances=5,
        price_type=price_type,
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )
    if fleet_type is not None:
        kwargs["fleet_type"] = fleet_type
    if fleet_role is not None:
        kwargs["fleet_role"] = fleet_role
    if allocation_strategy is not None:
        kwargs["allocation_strategy"] = allocation_strategy
    return AWSTemplate(**kwargs)

"""Shared fixtures for moto-based full-pipeline integration tests."""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from orb.providers.aws.utilities.aws_operations import AWSOperations
from tests.utilities.reset_singletons import reset_all_singletons

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_CONFIG_SOURCE = _PROJECT_ROOT / "config"

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Moto context
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def moto_aws():
    """Start moto mock_aws context for the duration of each test."""
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")  # nosec B105
    os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")  # nosec B105
    os.environ.setdefault("AWS_SESSION_TOKEN", "testing")  # nosec B105
    os.environ.setdefault("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        yield


# ---------------------------------------------------------------------------
# VPC / subnet / SG resources
# ---------------------------------------------------------------------------


@pytest.fixture
def moto_vpc_resources(moto_aws):
    """Create a VPC, 2 subnets, and 1 security group in moto eu-west-2."""
    ec2 = boto3.client("ec2", region_name=REGION)

    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]

    subnet_a = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=f"{REGION}a"
    )
    subnet_b = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.2.0/24", AvailabilityZone=f"{REGION}b"
    )
    subnet_ids = [subnet_a["Subnet"]["SubnetId"], subnet_b["Subnet"]["SubnetId"]]

    sg = ec2.create_security_group(
        GroupName="orb-test-sg", Description="ORB moto integration test SG", VpcId=vpc_id
    )
    sg_id = sg["GroupId"]

    return {"vpc_id": vpc_id, "subnet_ids": subnet_ids, "sg_id": sg_id}


# ---------------------------------------------------------------------------
# ORB config directory
# ---------------------------------------------------------------------------


@pytest.fixture
def orb_config_dir(tmp_path, moto_vpc_resources):
    """Generate a complete ORB config directory pointing at moto VPC resources.

    Writes config.json, aws_templates.json, and default_config.json into
    tmp_path/config/, sets ORB_CONFIG_DIR, and returns the config dir path.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    subnet_ids = moto_vpc_resources["subnet_ids"]
    sg_id = moto_vpc_resources["sg_id"]

    # --- config.json ---
    config_data = {
        "scheduler": {
            "type": "hostfactory",
            "config_root": str(config_dir),
        },
        "provider": {
            "providers": [
                {
                    "name": f"aws_moto_{REGION}",
                    "type": "aws",
                    "enabled": True,
                    "default": True,
                    "config": {
                        "region": REGION,
                    },
                    "template_defaults": {
                        "subnet_ids": subnet_ids,
                        "security_group_ids": [sg_id],
                    },
                }
            ]
        },
        "storage": {
            "strategy": "json",
            "default_storage_path": str(tmp_path / "data"),
            "json_strategy": {
                "storage_type": "single_file",
                "base_path": str(tmp_path / "data"),
                "filenames": {"single_file": "request_database.json"},
            },
        },
    }
    with open(config_dir / "config.json", "w") as f:
        json.dump(config_data, f, indent=2)

    # --- aws_templates.json ---
    # Load via the real scheduler pipeline (HF camelCase source → HF wire format)
    try:
        from tests.onaws.template_processor import TemplateProcessor

        templates_data = TemplateProcessor.generate_templates_programmatically("hostfactory")
    except Exception:
        # Fallback: copy the source file directly if programmatic generation fails
        src = _CONFIG_SOURCE / "aws_templates.json"
        if src.exists():
            shutil.copy2(src, config_dir / "aws_templates.json")
        templates_data = None

    if templates_data is not None:
        with open(config_dir / "aws_templates.json", "w") as f:
            json.dump(templates_data, f, indent=2)

    # --- default_config.json ---
    default_src = _CONFIG_SOURCE / "default_config.json"
    if default_src.exists():
        shutil.copy2(default_src, config_dir / "default_config.json")

    # Point ORB at this config directory
    os.environ["ORB_CONFIG_DIR"] = str(config_dir)

    yield config_dir

    # Cleanup env var after test
    os.environ.pop("ORB_CONFIG_DIR", None)


# ---------------------------------------------------------------------------
# Singleton reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset DI container and all singletons before and after each test."""
    from orb.infrastructure.di.container import reset_container

    reset_container()
    reset_all_singletons()
    yield
    reset_container()
    reset_all_singletons()


# ---------------------------------------------------------------------------
# AWS clients
# ---------------------------------------------------------------------------


@pytest.fixture
def ec2_client(moto_aws):
    """boto3 EC2 client backed by moto, eu-west-2."""
    return boto3.client("ec2", region_name=REGION)


@pytest.fixture
def autoscaling_client(moto_aws):
    """boto3 AutoScaling client backed by moto, eu-west-2."""
    return boto3.client("autoscaling", region_name=REGION)


# ---------------------------------------------------------------------------
# Handler factory helpers (shared with handler-level tests)
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


def _make_aws_client(region: str = REGION) -> AWSClient:
    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=region)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=region)
    aws_client.sts_client = boto3.client("sts", region_name=region)
    return aws_client


def _make_launch_template_manager(aws_client: AWSClient, logger) -> AWSLaunchTemplateManager:
    from orb.providers.aws.infrastructure.launch_template.manager import LaunchTemplateResult

    lt_manager = MagicMock(spec=AWSLaunchTemplateManager)

    def _create_or_update(template, request):
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
    return ASGHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


def make_ec2_fleet_handler(aws_client, logger, config_port) -> EC2FleetHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_launch_template_manager(aws_client, logger)
    return EC2FleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


def make_spot_fleet_handler(aws_client, logger, config_port) -> SpotFleetHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_launch_template_manager(aws_client, logger)
    return SpotFleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


def make_run_instances_handler(aws_client, logger, config_port) -> RunInstancesHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_launch_template_manager(aws_client, logger)
    return RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


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

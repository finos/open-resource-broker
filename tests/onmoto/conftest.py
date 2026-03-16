"""Shared fixtures for moto-based full-pipeline integration tests."""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

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
# Moto compatibility patches
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_moto_compat():
    """Patch moto-incompatible behaviours for all onmoto tests.

    1. AWSImageResolutionService.is_resolution_needed -> False
       Prevents SSM path resolution which moto cannot fulfil.

    2. AWSProvisioningAdapter._provision_via_handlers synthesises instances
       from instance_ids so the orchestration loop sees fulfilled_count > 0.
    """
    from orb.providers.aws.infrastructure.adapters.aws_provisioning_adapter import (
        AWSProvisioningAdapter,
    )

    _original_provision = AWSProvisioningAdapter._provision_via_handlers

    def _patched_provision(self, request, template, dry_run=False):
        result = _original_provision(self, request, template, dry_run=dry_run)
        if isinstance(result, dict) and not result.get("instances"):
            instance_ids = result.get("instance_ids") or result.get("resource_ids", [])
            iids = [i for i in instance_ids if i.startswith("i-")]
            if iids:
                result["instances"] = [{"instance_id": iid} for iid in iids]
        return result

    with (
        patch(
            "orb.providers.aws.infrastructure.services.aws_image_resolution_service"
            ".AWSImageResolutionService.is_resolution_needed",
            return_value=False,
        ),
        patch.object(AWSProvisioningAdapter, "_provision_via_handlers", _patched_provision),
    ):
        yield


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
                        "fleet_role": "arn:aws:iam::123456789012:role/aws-service-role/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet",
                    },
                    "template_defaults": {
                        "subnet_ids": subnet_ids,
                        "security_group_ids": [sg_id],
                    },
                    "handlers": {
                        "RunInstances": {"enabled": True, "handler_class": "RunInstancesHandler"},
                        "EC2Fleet": {"enabled": True, "handler_class": "EC2FleetHandler"},
                        "SpotFleet": {"enabled": True, "handler_class": "SpotFleetHandler"},
                        "ASG": {"enabled": True, "handler_class": "ASGHandler"},
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
    from orb.config.schemas.cleanup_schema import CleanupConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = prefix
    provider_defaults = ProviderDefaults(cleanup=CleanupConfig(enabled=False).model_dump())
    provider_config = MagicMock()
    provider_config.provider_defaults = {"aws": provider_defaults}
    config_port.get_provider_config.return_value = provider_config
    return config_port


def _make_moto_aws_client(region: str = REGION) -> AWSClient:
    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=region)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=region)
    aws_client.sts_client = boto3.client("sts", region_name=region)
    aws_client.ssm_client = boto3.client("ssm", region_name=region)
    return aws_client


def _make_launch_template_manager(aws_client: AWSClient, logger) -> AWSLaunchTemplateManager:
    from orb.providers.aws.infrastructure.launch_template.manager import LaunchTemplateResult

    lt_manager = MagicMock(spec=AWSLaunchTemplateManager)

    def _create_or_update(template, request):
        lt_name = f"{request.request_id}-{template.template_id}"
        try:
            resp = aws_client.ec2_client.create_launch_template(
                LaunchTemplateName=lt_name,
                TagSpecifications=[
                    {
                        "ResourceType": "launch-template",
                        "Tags": [
                            {"Key": "orb:request-id", "Value": str(request.request_id)},
                            {"Key": "orb:managed-by", "Value": "open-resource-broker"},
                        ],
                    }
                ],
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


def _inject_moto_factory(aws_client: AWSClient, logger, config_port) -> None:
    """Swap the DI-wired AWSProviderStrategy's internals for moto-backed ones."""
    from orb.domain.base.ports import ConfigurationPort
    from orb.infrastructure.di.container import get_container
    from orb.providers.aws.domain.template.value_objects import ProviderApi
    from orb.providers.aws.infrastructure.adapters.aws_provisioning_adapter import (
        AWSProvisioningAdapter,
    )
    from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
    from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory
    from orb.providers.aws.services.instance_operation_service import AWSInstanceOperationService
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()

    container = get_container()
    cfg_port = container.get(ConfigurationPort)
    registry._config_port = cfg_port
    provider_config = cfg_port.get_provider_config()
    if provider_config:
        for pi in provider_config.get_active_providers():
            if not registry.is_provider_instance_registered(pi.name):
                registry.ensure_provider_instance_registered_from_config(pi)

    strategy = registry.get_or_create_strategy("aws_moto_eu-west-2")
    if strategy is None:
        return

    lt_manager = _make_launch_template_manager(aws_client, logger)
    aws_ops = AWSOperations(aws_client, logger, cfg_port)
    factory = AWSHandlerFactory(aws_client=aws_client, logger=logger, config=cfg_port)

    factory._handlers[ProviderApi.ASG.value] = ASGHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=cfg_port,
    )
    factory._handlers[ProviderApi.EC2_FLEET.value] = EC2FleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=cfg_port,
    )
    factory._handlers[ProviderApi.RUN_INSTANCES.value] = RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=cfg_port,
    )
    factory._handlers[ProviderApi.SPOT_FLEET.value] = SpotFleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=cfg_port,
    )

    strategy._aws_client = aws_client
    handler_registry = strategy._get_handler_registry()
    handler_registry._handler_factory = factory
    handler_registry._handler_cache = dict(factory._handlers)

    machine_adapter = AWSMachineAdapter(aws_client=aws_client, logger=logger)
    provisioning_adapter = AWSProvisioningAdapter(
        aws_client=aws_client,
        logger=logger,
        provider_strategy=strategy,
        config_port=cfg_port,
    )
    instance_service = AWSInstanceOperationService(
        aws_client=aws_client,
        logger=logger,
        provisioning_adapter=provisioning_adapter,
        machine_adapter=machine_adapter,
        provider_name="aws_moto_eu-west-2",
        provider_type="aws",
    )
    strategy._instance_service = instance_service


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

"""Cross-cutting integration tests against moto-mocked AWS.

Covers:
- provider_data.resource_type assertions for ASG and RunInstances handlers
- Launch template tag verification for EC2Fleet and RunInstances handlers
- Idempotency: calling acquire_hosts twice with the same request_id
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Helpers (mirror test_provision_lifecycle.py pattern exactly)
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_config_port(prefix: str = "") -> Any:
    from orb.config.schemas.cleanup_schema import CleanupConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = prefix
    provider_defaults = ProviderDefaults(cleanup=CleanupConfig(enabled=False).model_dump())
    provider_config = MagicMock()
    provider_config.provider_defaults = {"aws": provider_defaults}
    config_port.get_provider_config.return_value = provider_config
    config_port.app_config = None
    return config_port


def _make_aws_client(region: str = REGION) -> AWSClient:
    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=region)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=region)
    aws_client.sts_client = boto3.client("sts", region_name=region)
    return aws_client


def _make_request(
    request_id: str = "req-cc-001",
    requested_count: int = 1,
    resource_ids: list[str] | None = None,
    provider_data: dict[str, Any] | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-cc"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _make_launch_template_manager_mock(aws_client: AWSClient, logger: Any) -> Any:
    """Moto-backed LT manager mock — same pattern as test_provision_lifecycle.py."""
    from orb.providers.aws.infrastructure.launch_template.manager import (
        AWSLaunchTemplateManager,
        LaunchTemplateResult,
    )

    lt_manager = MagicMock(spec=AWSLaunchTemplateManager)

    def _create_or_update(template: AWSTemplate, request: Any) -> LaunchTemplateResult:
        lt_name = f"orb-lt-{request.request_id}"
        # Check if the template already exists (idempotency)
        try:
            existing = aws_client.ec2_client.describe_launch_templates(
                LaunchTemplateNames=[lt_name]
            )
            lt = existing["LaunchTemplates"][0]
            return LaunchTemplateResult(
                template_id=lt["LaunchTemplateId"],
                version=str(lt["LatestVersionNumber"]),
                template_name=lt_name,
                is_new_template=False,
            )
        except Exception:
            pass  # template does not exist yet — create it below

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


def _make_launch_template_manager_real(aws_client: AWSClient, logger: Any, config_port: Any) -> Any:
    """Real AWSLaunchTemplateManager backed by moto — used for LT tag tests."""
    from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager

    return AWSLaunchTemplateManager(
        aws_client=aws_client,
        logger=logger,
        config_port=config_port,
        aws_native_spec_service=None,
    )


def _make_factory(aws_client: AWSClient, logger: Any, config_port: Any) -> AWSHandlerFactory:
    from orb.providers.aws.domain.template.value_objects import ProviderApi
    from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
    from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
    from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
    from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
    from orb.providers.aws.utilities.aws_operations import AWSOperations

    factory = AWSHandlerFactory(aws_client=aws_client, logger=logger, config=config_port)
    lt_manager = _make_launch_template_manager_mock(aws_client, logger)
    aws_ops = AWSOperations(aws_client, logger, config_port)

    factory._handlers[ProviderApi.ASG.value] = ASGHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    factory._handlers[ProviderApi.EC2_FLEET.value] = EC2FleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    factory._handlers[ProviderApi.RUN_INSTANCES.value] = RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )

    spot_handler = SpotFleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    original_build = spot_handler._config_builder.build

    def _patched_build(**kwargs: Any) -> dict:  # type: ignore[type-arg]
        config = original_build(**kwargs)
        tag_specs = config.get("TagSpecifications", [])
        config["TagSpecifications"] = [
            ts for ts in tag_specs if ts.get("ResourceType") != "instance"
        ]
        return config

    spot_handler._config_builder.build = _patched_build  # type: ignore[method-assign]
    factory._handlers[ProviderApi.SPOT_FLEET.value] = spot_handler

    return factory


def _make_factory_with_real_lt(
    aws_client: AWSClient, logger: Any, config_port: Any, handler_type: str
) -> AWSHandlerFactory:
    """Factory where the specified handler uses a real AWSLaunchTemplateManager."""
    from orb.providers.aws.domain.template.value_objects import ProviderApi
    from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
    from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
    from orb.providers.aws.utilities.aws_operations import AWSOperations

    factory = AWSHandlerFactory(aws_client=aws_client, logger=logger, config=config_port)
    real_lt = _make_launch_template_manager_real(aws_client, logger, config_port)
    aws_ops = AWSOperations(aws_client, logger, config_port)

    if handler_type == "EC2Fleet":
        factory._handlers[ProviderApi.EC2_FLEET.value] = EC2FleetHandler(
            aws_client=aws_client,
            logger=logger,
            aws_ops=aws_ops,
            launch_template_manager=real_lt,
            config_port=config_port,
        )
    elif handler_type == "RunInstances":
        factory._handlers[ProviderApi.RUN_INSTANCES.value] = RunInstancesHandler(
            aws_client=aws_client,
            logger=logger,
            aws_ops=aws_ops,
            launch_template_manager=real_lt,
            config_port=config_port,
        )

    return factory


# ---------------------------------------------------------------------------
# Template factories
# ---------------------------------------------------------------------------


def _asg_template(subnet_id: str, sg_id: str) -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-asg-cc",
        name="test-asg-cc",
        provider_api="ASG",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )


def _run_instances_template(subnet_id: str, sg_id: str) -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-run-cc",
        name="test-run-cc",
        provider_api="RunInstances",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )


def _ec2_fleet_template(subnet_id: str, sg_id: str) -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-fleet-cc",
        name="test-fleet-cc",
        provider_api="EC2Fleet",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        fleet_type="instant",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aws_client(moto_aws):
    return _make_aws_client()


@pytest.fixture
def subnet_id(moto_vpc_resources):
    return moto_vpc_resources["subnet_ids"][0]


@pytest.fixture
def sg_id(moto_vpc_resources):
    return moto_vpc_resources["sg_id"]


@pytest.fixture
def factory(aws_client):
    logger = _make_logger()
    config_port = _make_config_port()
    return _make_factory(aws_client, logger, config_port)


# ---------------------------------------------------------------------------
# Group 1 — provider_data.resource_type assertions
# ---------------------------------------------------------------------------


class TestProviderDataResourceType:
    def test_provider_data_resource_type_asg(self, factory, subnet_id, sg_id):
        """ASG handler sets provider_data['resource_type'] == 'asg'."""
        handler = factory.create_handler("ASG")
        template = _asg_template(subnet_id, sg_id)
        request = _make_request(request_id="cc-asg-rt-001", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert result["provider_data"]["resource_type"] == "asg"

    def test_provider_data_resource_type_run_instances(self, factory, subnet_id, sg_id):
        """RunInstances handler sets provider_data['resource_type'] == 'run_instances'."""
        handler = factory.create_handler("RunInstances")
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="cc-run-rt-001", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert result["provider_data"]["resource_type"] == "run_instances"


# ---------------------------------------------------------------------------
# Group 2 — Launch template tags (real AWSLaunchTemplateManager)
# ---------------------------------------------------------------------------


class TestLaunchTemplateTags:
    def test_launch_template_created_with_correct_tags_ec2fleet(self, moto_aws, moto_vpc_resources):
        """EC2Fleet handler creates a launch template tagged with orb:request-id and orb:managed-by."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()

        factory = _make_factory_with_real_lt(aws_client, logger, config_port, "EC2Fleet")
        handler = factory.create_handler("EC2Fleet")
        template = _ec2_fleet_template(subnet_id, sg_id)
        request = _make_request(request_id="cc-lt-fleet-001", requested_count=1)

        handler.acquire_hosts(request, template)

        ec2 = boto3.client("ec2", region_name=REGION)
        resp = ec2.describe_launch_templates(
            Filters=[{"Name": "tag:orb:request-id", "Values": ["cc-lt-fleet-001"]}]
        )
        templates = resp.get("LaunchTemplates", [])
        assert len(templates) >= 1, (
            "Expected at least one launch template tagged with the request ID"
        )

        tags = {t["Key"]: t["Value"] for t in templates[0].get("Tags", [])}
        assert tags.get("orb:request-id") == "cc-lt-fleet-001"
        assert tags.get("orb:managed-by") == "open-resource-broker"

    def test_launch_template_created_with_correct_tags_run_instances(
        self, moto_aws, moto_vpc_resources
    ):
        """RunInstances handler creates a launch template tagged with orb:request-id and orb:managed-by."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        aws_client = _make_aws_client()
        logger = _make_logger()
        config_port = _make_config_port()

        factory = _make_factory_with_real_lt(aws_client, logger, config_port, "RunInstances")
        handler = factory.create_handler("RunInstances")
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="cc-lt-run-001", requested_count=1)

        handler.acquire_hosts(request, template)

        ec2 = boto3.client("ec2", region_name=REGION)
        resp = ec2.describe_launch_templates(
            Filters=[{"Name": "tag:orb:request-id", "Values": ["cc-lt-run-001"]}]
        )
        templates = resp.get("LaunchTemplates", [])
        assert len(templates) >= 1, (
            "Expected at least one launch template tagged with the request ID"
        )

        tags = {t["Key"]: t["Value"] for t in templates[0].get("Tags", [])}
        assert tags.get("orb:request-id") == "cc-lt-run-001"
        assert tags.get("orb:managed-by") == "open-resource-broker"


# ---------------------------------------------------------------------------
# Group 3 — Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_acquire_twice_same_request_id_run_instances(self, factory, subnet_id, sg_id):
        """Calling acquire_hosts twice with the same request_id for RunInstances.

        The real AWSLaunchTemplateManager deduplicates via describe_launch_templates,
        but the mock LT manager used here will attempt a second create_launch_template
        call which moto rejects with AlreadyExists — the handler catches this and
        still returns a successful result on both calls.
        """
        handler = factory.create_handler("RunInstances")
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="cc-idem-run-001", requested_count=1)

        result1 = handler.acquire_hosts(request, template)
        result2 = handler.acquire_hosts(request, template)

        assert result1["success"] is True
        assert result2["success"] is True

    def test_acquire_twice_same_request_id_ec2fleet(self, factory, subnet_id, sg_id, ec2_client):
        """Calling acquire_hosts twice with the same request_id for EC2Fleet.

        Each call creates a new fleet (the handler does not deduplicate at the fleet
        level), so two fleet IDs are returned — both distinct and valid.
        """
        handler = factory.create_handler("EC2Fleet")
        template = _ec2_fleet_template(subnet_id, sg_id)
        request = _make_request(request_id="cc-idem-fleet-001", requested_count=1)

        result1 = handler.acquire_hosts(request, template)
        result2 = handler.acquire_hosts(request, template)

        assert result1["success"] is True
        assert result2["success"] is True

        fleet_id1 = result1["resource_ids"][0]
        fleet_id2 = result2["resource_ids"][0]

        # Both fleets must exist in AWS
        resp = ec2_client.describe_fleets(FleetIds=[fleet_id1, fleet_id2])
        found_ids = {f["FleetId"] for f in resp["Fleets"]}
        assert fleet_id1 in found_ids
        assert fleet_id2 in found_ids

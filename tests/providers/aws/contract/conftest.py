"""Moto-backed fixtures for AWS provider contract tests.

Supplies all fixtures required by the base contract classes:
    provider_under_test, valid_provision_request, valid_template,
    provisioned_resource_ids, template_provider, valid_template_for_validation,
    invalid_template_for_validation, validation_adapter, known_provider_api.
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from orb.providers.aws.utilities.aws_operations import AWSOperations
from tests.utilities.reset_singletons import reset_all_singletons

REGION = "eu-west-2"

SPOT_FLEET_ROLE = (
    "arn:aws:iam::123456789012:role/aws-service-role/"
    "spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
)


# ---------------------------------------------------------------------------
# Singleton reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singletons():
    from orb.infrastructure.di.container import reset_container

    reset_container()
    reset_all_singletons()
    yield
    reset_container()
    reset_all_singletons()


# ---------------------------------------------------------------------------
# Moto context
# ---------------------------------------------------------------------------


@pytest.fixture
def moto_aws():
    import os

    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")  # nosec B105
    os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")  # nosec B105
    os.environ.setdefault("AWS_SESSION_TOKEN", "testing")  # nosec B105
    os.environ.setdefault("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        yield


@pytest.fixture
def moto_vpc_resources(moto_aws):
    ec2 = boto3.client("ec2", region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    subnet_a = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=f"{REGION}a"
    )
    subnet_b = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.2.0/24", AvailabilityZone=f"{REGION}b"
    )
    sg = ec2.create_security_group(
        GroupName="orb-contract-sg", Description="ORB contract test SG", VpcId=vpc_id
    )
    return {
        "vpc_id": vpc_id,
        "subnet_ids": [subnet_a["Subnet"]["SubnetId"], subnet_b["Subnet"]["SubnetId"]],
        "sg_id": sg["GroupId"],
    }


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_config_port(prefix: str = "") -> Any:
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


def _make_lt_manager(aws_client: AWSClient, logger: Any) -> Any:
    from orb.providers.aws.infrastructure.launch_template.manager import LaunchTemplateResult

    lt_manager = MagicMock(spec=AWSLaunchTemplateManager)

    def _create_or_update(template: AWSTemplate, request: Any) -> LaunchTemplateResult:
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


def _make_request(
    request_id: str = "req-contract-001",
    requested_count: int = 1,
    resource_ids: list[str] | None = None,
    provider_data: dict | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-contract"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _build_asg_handler(aws_client: AWSClient, logger: Any, config_port: Any) -> ASGHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_lt_manager(aws_client, logger)
    return ASGHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


def _build_run_instances_handler(
    aws_client: AWSClient, logger: Any, config_port: Any
) -> RunInstancesHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_lt_manager(aws_client, logger)
    return RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


# ---------------------------------------------------------------------------
# Shared fixtures consumed by contract base classes
# ---------------------------------------------------------------------------


@pytest.fixture
def _aws_client(moto_vpc_resources):
    """boto3-backed AWSClient inside the moto context."""
    return _make_aws_client()


@pytest.fixture
def _logger():
    return _make_logger()


@pytest.fixture
def _config_port():
    return _make_config_port()


class _HandlerMonitoringAdapter:
    """Thin wrapper that adds get_provider_info to a raw AWS handler."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler

    def acquire_hosts(self, request: Any, template: Any) -> dict:
        return self._handler.acquire_hosts(request, template)

    def release_hosts(self, machine_ids: list) -> None:
        return self._handler.release_hosts(machine_ids)

    def check_hosts_status(self, request: Any) -> list:
        return self._handler.check_hosts_status(request)

    def get_provider_info(self) -> dict:
        return {"provider_type": "aws", "handler": type(self._handler).__name__}


@pytest.fixture
def provider_under_test(_aws_client, _logger, _config_port):
    """ASG handler wrapped to satisfy acquire_hosts / release_hosts / get_provider_info."""
    handler = _build_asg_handler(_aws_client, _logger, _config_port)
    return _HandlerMonitoringAdapter(handler)


@pytest.fixture
def valid_provision_request():
    return _make_request(request_id="contract-req-001", requested_count=1)


@pytest.fixture
def valid_template(moto_vpc_resources):
    subnet_id = moto_vpc_resources["subnet_ids"][0]
    sg_id = moto_vpc_resources["sg_id"]
    return AWSTemplate(
        template_id="tpl-contract-asg",
        name="contract-asg",
        provider_api="ASG",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "contract-test"},
    )


@pytest.fixture
def provisioned_resource_ids(moto_vpc_resources, _aws_client, _logger, _config_port):
    """Provision via RunInstances (moto fully supports it) and yield (handler, ids, status_req)."""
    handler = _build_run_instances_handler(_aws_client, _logger, _config_port)
    subnet_id = moto_vpc_resources["subnet_ids"][0]
    sg_id = moto_vpc_resources["sg_id"]
    template = AWSTemplate(
        template_id="tpl-contract-run",
        name="contract-run",
        provider_api="RunInstances",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "contract-test"},
    )
    request = _make_request(request_id="contract-mon-001", requested_count=1)
    result = handler.acquire_hosts(request, template)
    instance_ids = result.get("provider_data", {}).get("instance_ids", [])
    reservation_id = result["resource_ids"][0]
    status_request = _make_request(
        request_id="contract-mon-001",
        resource_ids=[reservation_id],
        provider_data={"instance_ids": instance_ids, "reservation_id": reservation_id},
    )
    yield handler, instance_ids, status_request


@pytest.fixture
def template_provider(_aws_client, _logger, _config_port):
    """AWSHandlerFactory wrapped to satisfy ProviderTemplatePort shape."""

    class _TemplateProviderAdapter:
        def __init__(self, factory: AWSHandlerFactory) -> None:
            self._factory = factory

        def get_available_templates(self) -> list:
            return self._factory.generate_example_templates()

        def validate_template(self, template: Any) -> bool:
            return (
                hasattr(template, "provider_api")
                and template.provider_api is not None
                and len(str(template.provider_api)) > 0
            )

    factory = AWSHandlerFactory(aws_client=_aws_client, logger=_logger, config=_config_port)
    return _TemplateProviderAdapter(factory)


@pytest.fixture
def valid_template_for_validation(moto_vpc_resources):
    subnet_id = moto_vpc_resources["subnet_ids"][0]
    sg_id = moto_vpc_resources["sg_id"]
    return AWSTemplate(
        template_id="tpl-valid",
        name="valid-template",
        provider_api="ASG",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={},
    )


@pytest.fixture
def invalid_template_for_validation():
    """Template with no provider_api — should be rejected."""
    tpl = MagicMock()
    tpl.provider_api = None
    tpl.template_id = "tpl-invalid"
    tpl.name = "invalid-template"
    return tpl


@pytest.fixture
def validation_adapter(_logger):
    from unittest.mock import Mock

    from orb.providers.aws.configuration.validator import AWSProviderConfig
    from orb.providers.aws.infrastructure.adapters.aws_validation_adapter import (
        AWSValidationAdapter,
    )

    config = Mock(spec=AWSProviderConfig)
    config.handlers = Mock()
    config.handlers.capabilities = {}
    return AWSValidationAdapter(config, _logger)


@pytest.fixture
def known_provider_api():
    return "ASG"

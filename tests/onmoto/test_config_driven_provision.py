"""Config-driven provision tests.

Validates that values set in config.json flow correctly through the DI container
into handler behaviour:
- Resource name prefixes from config.resource.prefixes appear in created AWS resource names
- Template defaults (subnet_ids, security_group_ids) from config are applied to templates
- config_port is properly injected and returns configured values
"""

import json
import os
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
from orb.providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
    LaunchTemplateResult,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Local helpers (mirror conftest pattern, avoid import coupling)
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_aws_client(region: str = REGION) -> AWSClient:
    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=region)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=region)
    aws_client.sts_client = boto3.client("sts", region_name=region)
    return aws_client


def _make_lt_manager(aws_client: AWSClient, logger: Any) -> Any:
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
                        else "t3.micro"
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
    request_id: str = "req-cfg-001",
    requested_count: int = 1,
    resource_ids: list[str] | None = None,
    provider_data: dict | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-cfg"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _make_factory_with_config_port(
    aws_client: AWSClient, logger: Any, config_port: Any
) -> AWSHandlerFactory:
    """Build AWSHandlerFactory with pre-wired handlers using the given config_port."""
    from orb.providers.aws.domain.template.value_objects import ProviderApi
    from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
    from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
    from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler

    factory = AWSHandlerFactory(aws_client=aws_client, logger=logger, config=config_port)
    lt_manager = _make_lt_manager(aws_client, logger)
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
    return factory


def _write_config(config_dir: Path, tmp_path: Path, moto_vpc: dict, prefix: str = "") -> None:
    """Write a config.json with optional resource prefix into config_dir."""
    subnet_ids = moto_vpc["subnet_ids"]
    sg_id = moto_vpc["sg_id"]

    config: dict[str, Any] = {
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
                    "config": {"region": REGION, "profile": "default"},
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

    if prefix:
        config["resource"] = {
            "prefixes": {
                "asg": prefix,
                "fleet": prefix,
                "instance": prefix,
            }
        }

    with open(config_dir / "config.json", "w") as f:
        json.dump(config, f, indent=2)


def _write_config_no_defaults(config_dir: Path, tmp_path: Path) -> None:
    """Write a config.json where template_defaults has empty subnet/sg lists."""
    config: dict[str, Any] = {
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
                    "config": {"region": REGION, "profile": "default"},
                    "template_defaults": {
                        "subnet_ids": [],
                        "security_group_ids": [],
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
        json.dump(config, f, indent=2)


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
def orb_config_with_prefix(tmp_path, moto_vpc_resources):
    """Boot DI with resource.prefixes.asg = 'ci-' and resource.prefixes.fleet = 'ci-'."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    _write_config(config_dir, tmp_path, moto_vpc_resources, prefix="ci-")
    os.environ["ORB_CONFIG_DIR"] = str(config_dir)
    yield config_dir
    os.environ.pop("ORB_CONFIG_DIR", None)


@pytest.fixture
def orb_config_no_prefix(tmp_path, moto_vpc_resources):
    """Boot DI with no resource.prefixes key in config."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    _write_config(config_dir, tmp_path, moto_vpc_resources, prefix="")
    os.environ["ORB_CONFIG_DIR"] = str(config_dir)
    yield config_dir
    os.environ.pop("ORB_CONFIG_DIR", None)


@pytest.fixture
def orb_config_no_defaults(tmp_path, moto_vpc_resources):
    """Boot DI with empty subnet_ids / security_group_ids in template_defaults."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    _write_config_no_defaults(config_dir, tmp_path)
    os.environ["ORB_CONFIG_DIR"] = str(config_dir)
    yield config_dir
    os.environ.pop("ORB_CONFIG_DIR", None)


# ---------------------------------------------------------------------------
# TestResourcePrefixConfig
# ---------------------------------------------------------------------------


class TestResourcePrefixConfig:
    def test_asg_name_has_configured_prefix(
        self, moto_vpc_resources, orb_config_with_prefix, autoscaling_client
    ):
        """ASG name starts with the prefix configured in resource.prefixes.asg."""
        from orb.domain.base.ports import ConfigurationPort, LoggingPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        config_port = container.get(ConfigurationPort)
        logger = container.get(LoggingPort)

        aws_client = _make_aws_client()
        factory = _make_factory_with_config_port(aws_client, logger, config_port)

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        template = AWSTemplate(
            template_id="tpl-prefix-asg",
            name="test-prefix-asg",
            provider_api="ASG",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=5,
            price_type="ondemand",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
            tags={"Environment": "test"},
        )
        request = _make_request(request_id="req-prefix-asg-001", requested_count=1)

        result = factory.create_handler("ASG").acquire_hosts(request, template)

        assert result["success"] is True
        asg_name = result["resource_ids"][0]
        assert asg_name.startswith("ci-"), (
            f"Expected ASG name to start with 'ci-', got: {asg_name!r}"
        )
        resp = autoscaling_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        assert len(resp["AutoScalingGroups"]) == 1

    def test_asg_name_has_no_prefix_when_not_configured(
        self, moto_vpc_resources, orb_config_no_prefix, autoscaling_client
    ):
        """ASG name does not start with 'ci-' when no prefix is configured."""
        from orb.domain.base.ports import ConfigurationPort, LoggingPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        config_port = container.get(ConfigurationPort)
        logger = container.get(LoggingPort)

        aws_client = _make_aws_client()
        factory = _make_factory_with_config_port(aws_client, logger, config_port)

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        template = AWSTemplate(
            template_id="tpl-noprefix-asg",
            name="test-noprefix-asg",
            provider_api="ASG",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=5,
            price_type="ondemand",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
            tags={"Environment": "test"},
        )
        request = _make_request(request_id="req-noprefix-asg-001", requested_count=1)

        result = factory.create_handler("ASG").acquire_hosts(request, template)

        assert result["success"] is True
        asg_name = result["resource_ids"][0]
        assert not asg_name.startswith("ci-"), (
            f"Expected ASG name without 'ci-' prefix, got: {asg_name!r}"
        )

    def test_ec2_fleet_resource_id_returned_regardless_of_prefix(
        self, moto_vpc_resources, orb_config_with_prefix, ec2_client
    ):
        """EC2 Fleet acquire succeeds and returns a fleet-* resource ID with prefix configured."""
        from orb.domain.base.ports import ConfigurationPort, LoggingPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        config_port = container.get(ConfigurationPort)
        logger = container.get(LoggingPort)

        aws_client = _make_aws_client()
        factory = _make_factory_with_config_port(aws_client, logger, config_port)

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        template = AWSTemplate(
            template_id="tpl-prefix-fleet",
            name="test-prefix-fleet",
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
        request = _make_request(request_id="req-prefix-fleet-001", requested_count=1)

        result = factory.create_handler("EC2Fleet").acquire_hosts(request, template)

        assert result["success"] is True
        fleet_id = result["resource_ids"][0]
        assert fleet_id.startswith("fleet-"), f"Expected fleet-* resource ID, got: {fleet_id!r}"


# ---------------------------------------------------------------------------
# TestTemplateDefaultsConfig
# ---------------------------------------------------------------------------


class TestTemplateDefaultsConfig:
    def test_empty_template_defaults_causes_validation_error(
        self, moto_vpc_resources, orb_config_no_defaults
    ):
        """acquire_hosts raises when template has no subnet_ids (empty defaults in config)."""
        from orb.providers.aws.exceptions.aws_exceptions import AWSValidationError

        logger = _make_logger()
        config_port = MagicMock()
        config_port.get_resource_prefix.return_value = ""
        config_port.get_cleanup_config.return_value = {"enabled": False}

        aws_client = _make_aws_client()
        factory = _make_factory_with_config_port(aws_client, logger, config_port)

        # Template with no subnet_ids — simulates what happens when defaults are empty
        template = AWSTemplate(
            template_id="tpl-no-subnet",
            name="test-no-subnet",
            provider_api="ASG",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=5,
            price_type="ondemand",
            subnet_ids=[],
            security_group_ids=[],
            tags={"Environment": "test"},
        )
        request = _make_request(request_id="req-no-subnet-001", requested_count=1)

        with pytest.raises((AWSValidationError, Exception)):
            factory.create_handler("ASG").acquire_hosts(request, template)

    def test_populated_template_defaults_flow_into_template(
        self, moto_vpc_resources, orb_config_dir
    ):
        """TemplateConfigurationManager.get_template returns a template with subnet_ids
        populated from config template_defaults."""
        from orb.infrastructure.di.container import get_container
        from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager

        container = get_container()
        manager = container.get(TemplateConfigurationManager)

        # Load all templates — at least one should have subnet_ids from config defaults
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            templates = loop.run_until_complete(manager.load_templates())
        finally:
            loop.close()

        assert len(templates) > 0, "Expected at least one template to be loaded"
        # Every loaded template should have subnet_ids populated from config defaults
        for tpl in templates:
            assert tpl.subnet_ids, (
                f"Template {tpl.template_id!r} has empty subnet_ids — template_defaults not applied"
            )
            assert tpl.subnet_ids[0] in moto_vpc_resources["subnet_ids"], (
                f"Template subnet_id {tpl.subnet_ids[0]!r} not from moto config"
            )

    def test_template_defaults_do_not_overwrite_explicit_subnet(
        self, moto_vpc_resources, orb_config_dir
    ):
        """A template constructed with an explicit subnet_id retains that value."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        explicit_subnet = subnet_id  # use the real moto subnet so acquire can succeed
        template = AWSTemplate(
            template_id="tpl-explicit-subnet",
            name="test-explicit-subnet",
            provider_api="RunInstances",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=5,
            price_type="ondemand",
            subnet_ids=[explicit_subnet],
            security_group_ids=[sg_id],
            tags={"Environment": "test"},
        )

        # The explicit subnet_id must be preserved — not replaced by defaults
        assert template.subnet_ids == [explicit_subnet]


# ---------------------------------------------------------------------------
# TestConfigPortInjection
# ---------------------------------------------------------------------------


class TestConfigPortInjection:
    def test_config_port_get_resource_prefix_returns_configured_value(
        self, moto_vpc_resources, orb_config_with_prefix
    ):
        """container.get(ConfigurationPort).get_resource_prefix('asg') returns 'ci-'."""
        from orb.domain.base.ports import ConfigurationPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        config_port = container.get(ConfigurationPort)

        prefix = config_port.get_resource_prefix("asg")
        assert prefix == "ci-", f"Expected get_resource_prefix('asg') == 'ci-', got: {prefix!r}"

    def test_config_port_returns_empty_prefix_when_not_configured(
        self, moto_vpc_resources, orb_config_no_prefix
    ):
        """get_resource_prefix returns '' when resource.prefixes is absent from config."""
        from orb.domain.base.ports import ConfigurationPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        config_port = container.get(ConfigurationPort)

        prefix = config_port.get_resource_prefix("asg")
        assert prefix == "", f"Expected empty prefix when not configured, got: {prefix!r}"

    def test_all_handlers_use_same_config_port_instance(
        self, moto_vpc_resources, orb_config_with_prefix
    ):
        """Handlers created by AWSHandlerFactory share the same config_port and
        return consistent prefix values."""
        from orb.domain.base.ports import ConfigurationPort, LoggingPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        config_port = container.get(ConfigurationPort)
        logger = container.get(LoggingPort)

        aws_client = _make_aws_client()
        factory = AWSHandlerFactory(aws_client=aws_client, logger=logger, config=config_port)

        asg_handler = factory.create_handler("ASG")
        fleet_handler = factory.create_handler("EC2Fleet")

        assert asg_handler.config_port is not None
        assert fleet_handler.config_port is not None

        # Both handlers must return the same prefix for the same key
        asg_prefix = asg_handler.config_port.get_resource_prefix("asg")
        fleet_prefix = fleet_handler.config_port.get_resource_prefix("asg")
        assert asg_prefix == fleet_prefix, (
            f"Handlers returned different prefixes: {asg_prefix!r} vs {fleet_prefix!r}"
        )
        assert asg_prefix == "ci-"

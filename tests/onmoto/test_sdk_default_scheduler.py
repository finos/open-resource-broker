"""SDK end-to-end tests with default scheduler against moto-mocked AWS.

Exercises the full ORBClient lifecycle with scheduler.type=default and
snake_case templates — initialize, method discovery, list_templates,
create_request, get_request_status, full lifecycle, cleanup.

No real AWS or network calls — all AWS interactions go through moto.
"""

import json
import logging
import re
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

logger = logging.getLogger(__name__)

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

REGION = "eu-west-2"
REQUEST_ID_RE = re.compile(r"^req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

pytestmark = [pytest.mark.moto, pytest.mark.sdk]

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_CONFIG_SOURCE = _PROJECT_ROOT / "config"


# ---------------------------------------------------------------------------
# Moto compatibility patches
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_moto_compat():
    """Patch moto-incompatible behaviours for all tests in this module."""
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
# Default-scheduler config fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def moto_aws():
    """Start moto mock_aws context for the duration of each test."""
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
        GroupName="orb-default-sched-sg",
        Description="ORB default scheduler moto test SG",
        VpcId=vpc_id,
    )
    sg_id = sg["GroupId"]

    return {"vpc_id": vpc_id, "subnet_ids": subnet_ids, "sg_id": sg_id}


@pytest.fixture
def orb_config_dir_default(tmp_path, moto_vpc_resources):
    """Generate an ORB config directory with scheduler.type=default and snake_case templates."""
    import os

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    subnet_ids = moto_vpc_resources["subnet_ids"]
    sg_id = moto_vpc_resources["sg_id"]

    config_data = {
        "scheduler": {
            "type": "default",
            "config_root": str(config_dir),
        },
        "provider": {
            "providers": [
                {
                    "name": f"aws_moto_{REGION}",
                    "type": "aws",
                    "enabled": True,
                    "default": True,
                    "config": {"region": REGION},
                    "handlers": {
                        "RunInstances": {"enabled": True, "handler_class": "RunInstancesHandler"},
                        "EC2Fleet": {"enabled": True, "handler_class": "EC2FleetHandler"},
                        "SpotFleet": {"enabled": True, "handler_class": "SpotFleetHandler"},
                        "ASG": {"enabled": True, "handler_class": "ASGHandler"},
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

    # Generate snake_case templates via the default scheduler strategy
    try:
        from tests.onaws.template_processor import TemplateProcessor

        templates_data = TemplateProcessor.generate_templates_programmatically("default")
    except Exception as exc:
        logger.warning("TemplateProcessor failed, using fallback templates: %s", exc)
        # Fallback: build a minimal snake_case template set
        templates_data = {
            "scheduler_type": "default",
            "templates": [
                {
                    "template_id": "RunInstances-OnDemand",
                    "name": "RunInstances OnDemand",
                    "provider_type": "aws",
                    "provider_api": "RunInstances",
                    "price_type": "ondemand",
                    "instance_type": "t3.micro",
                    "machine_types": {"t3.micro": 1},
                    "image_id": "ami-12345678",
                    "max_instances": 10,
                    "subnet_ids": subnet_ids,
                    "security_group_ids": [sg_id],
                }
            ],
        }

    with open(config_dir / "aws_templates.json", "w") as f:
        json.dump(templates_data, f, indent=2)

    default_src = _CONFIG_SOURCE / "default_config.json"
    if default_src.exists():
        shutil.copy2(default_src, config_dir / "default_config.json")

    os.environ["ORB_CONFIG_DIR"] = str(config_dir)
    yield config_dir
    os.environ.pop("ORB_CONFIG_DIR", None)


# ---------------------------------------------------------------------------
# Singleton reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singletons():
    from orb.infrastructure.di.container import reset_container
    from tests.utilities.reset_singletons import reset_all_singletons

    reset_container()
    reset_all_singletons()
    yield
    reset_container()
    reset_all_singletons()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_request_id(result) -> str | None:
    if isinstance(result, dict):
        return (
            result.get("requestId") or result.get("request_id") or result.get("created_request_id")
        )
    return getattr(result, "request_id", None) or getattr(result, "created_request_id", None)


def _extract_status(result) -> str:
    if isinstance(result, dict):
        requests = result.get("requests", [])
        if requests and isinstance(requests[0], dict):
            return requests[0].get("status", "unknown")
        return result.get("status", "unknown")
    return getattr(result, "status", "unknown")


def _extract_templates(result) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("templates", [])
    templates = getattr(result, "templates", None)
    return list(templates) if templates is not None else []


def _get_template_field(tpl, *keys: str):
    for key in keys:
        val = tpl.get(key) if isinstance(tpl, dict) else getattr(tpl, key, None)
        if val is not None:
            return val
    return None


def _make_moto_aws_client():
    from orb.providers.aws.infrastructure.aws_client import AWSClient

    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=REGION)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=REGION)
    aws_client.sts_client = boto3.client("sts", region_name=REGION)
    aws_client.ssm_client = boto3.client("ssm", region_name=REGION)
    return aws_client


def _make_logger():
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _inject_moto_factory(aws_client, logger) -> None:
    """Wire the DI-bootstrapped AWSProviderStrategy to use moto-backed handlers."""
    from orb.domain.base.ports import ConfigurationPort
    from orb.infrastructure.di.container import get_container
    from orb.providers.aws.domain.template.value_objects import ProviderApi
    from orb.providers.aws.infrastructure.adapters.aws_provisioning_adapter import (
        AWSProvisioningAdapter,
    )
    from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
    from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory
    from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
    from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
    from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
    from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
    from orb.providers.aws.infrastructure.launch_template.manager import (
        AWSLaunchTemplateManager,
        LaunchTemplateResult,
    )
    from orb.providers.aws.services.instance_operation_service import AWSInstanceOperationService
    from orb.providers.aws.utilities.aws_operations import AWSOperations
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()
    registry._strategy_cache.pop(f"aws_moto_{REGION}", None)

    container = get_container()
    cfg_port = container.get(ConfigurationPort)
    provider_config = cfg_port.get_provider_config()
    if provider_config:
        for pi in provider_config.get_active_providers():
            if not registry.is_provider_instance_registered(pi.name):
                registry.ensure_provider_instance_registered_from_config(pi)

    strategy = registry.get_or_create_strategy(f"aws_moto_{REGION}")
    if strategy is None:
        return

    # Build moto-backed launch template manager
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
        except Exception as exc:
            logger.warning("Launch template creation failed, using mock: %s", exc)
            lt_id = "lt-mock"
            version = "1"
        return LaunchTemplateResult(
            template_id=lt_id,
            version=version,
            template_name=lt_name,
            is_new_template=True,
        )

    lt_manager.create_or_update_launch_template.side_effect = _create_or_update

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
        provider_name=f"aws_moto_{REGION}",
        provider_type="aws",
    )
    strategy._instance_service = instance_service


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSDKDefaultSchedulerInit:
    """ORBClient initializes correctly with default scheduler config."""

    @pytest.mark.asyncio
    async def test_sdk_initializes_with_default_scheduler(self, orb_config_dir_default, moto_aws):
        """SDK initializes successfully when scheduler.type=default."""
        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir_default / "config.json").read_text())
        assert config_data["scheduler"]["type"] == "default"

        async with ORBClient(app_config=config_data) as sdk:
            assert sdk.initialized

    @pytest.mark.asyncio
    async def test_sdk_discovers_methods_with_default_scheduler(
        self, orb_config_dir_default, moto_aws
    ):
        """SDK discovers CQRS methods when using default scheduler."""
        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir_default / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            methods = sdk.list_available_methods()
            assert len(methods) > 0
            assert "list_templates" in methods
            assert "create_request" in methods

    @pytest.mark.asyncio
    async def test_sdk_get_stats_with_default_scheduler(self, orb_config_dir_default, moto_aws):
        """get_stats() returns expected shape with default scheduler."""
        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir_default / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            stats = sdk.get_stats()
            assert stats["initialized"] is True
            assert stats["methods_discovered"] > 0
            assert "available_methods" in stats

    @pytest.mark.asyncio
    async def test_sdk_cleanup_resets_state(self, orb_config_dir_default, moto_aws):
        """cleanup() resets initialized state after default scheduler init."""
        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir_default / "config.json").read_text())

        sdk = ORBClient(app_config=config_data)
        await sdk.initialize()
        assert sdk.initialized

        await sdk.cleanup()
        assert not sdk.initialized


class TestSDKDefaultSchedulerTemplates:
    """Template operations with default scheduler (snake_case format)."""

    @pytest.mark.asyncio
    async def test_list_templates_returns_snake_case_templates(
        self, orb_config_dir_default, moto_aws
    ):
        """list_templates() returns templates loaded from snake_case default scheduler format."""
        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir_default / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            result = await sdk.list_templates()
            assert result is not None

            templates = _extract_templates(result)
            assert len(templates) > 0, "list_templates() returned no templates"

            for tpl in templates:
                tid = _get_template_field(tpl, "template_id", "templateId")
                assert tid, f"Template missing template_id: {tpl}"

    @pytest.mark.asyncio
    async def test_list_templates_contains_run_instances_template(
        self, orb_config_dir_default, moto_aws
    ):
        """list_templates() includes RunInstances-OnDemand template with default scheduler."""
        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir_default / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            result = await sdk.list_templates()
            templates = _extract_templates(result)
            known_ids = {
                _get_template_field(tpl, "template_id", "templateId") for tpl in templates
            } - {None}
            assert len(known_ids) > 0
            assert "RunInstances-OnDemand" in known_ids, (
                f"'RunInstances-OnDemand' not found. Got: {sorted(known_ids)}"
            )


class TestSDKDefaultSchedulerRequests:
    """Request lifecycle with default scheduler."""

    @pytest.mark.asyncio
    async def test_create_request_returns_request_id(
        self, orb_config_dir_default, moto_aws, moto_vpc_resources
    ):
        """create_request() returns a valid request_id with default scheduler."""
        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir_default / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger)

            result = await sdk.create_request(template_id="RunInstances-OnDemand", count=1)
            request_id = _extract_request_id(result)

            assert request_id is not None, f"No request_id in response: {result}"
            assert REQUEST_ID_RE.match(request_id), (
                f"request_id {request_id!r} does not match expected pattern"
            )

    @pytest.mark.asyncio
    async def test_get_request_status_after_create(
        self, orb_config_dir_default, moto_aws, moto_vpc_resources
    ):
        """get_request() returns a well-formed status response after create_request()."""
        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir_default / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger)

            create_result = await sdk.create_request(template_id="RunInstances-OnDemand", count=1)
            request_id = _extract_request_id(create_result)
            assert request_id, f"No request_id in create response: {create_result}"

            methods = sdk.list_available_methods()
            if "get_request_status" in methods:
                status_result = await sdk.get_request_status(request_id=request_id)  # type: ignore[attr-defined]
            else:
                status_result = await sdk.get_request(request_id=request_id)

            assert status_result is not None
            status = _extract_status(status_result)
            assert status in {"running", "complete", "complete_with_error", "pending", "unknown"}, (
                f"Unexpected status: {status!r}"
            )

    @pytest.mark.asyncio
    async def test_full_lifecycle_with_default_scheduler(
        self, orb_config_dir_default, moto_aws, moto_vpc_resources
    ):
        """Full cycle: create_request -> get_request -> create_return_request with default scheduler.

        Verifies that the default scheduler path (snake_case templates) produces
        the same observable behaviour as the hostfactory path.
        """
        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir_default / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger)

            # 1. Verify template exists
            templates_result = await sdk.list_templates()
            known_ids = {
                _get_template_field(tpl, "template_id", "templateId")
                for tpl in _extract_templates(templates_result)
            } - {None}
            assert "RunInstances-OnDemand" in known_ids, (
                f"'RunInstances-OnDemand' not in templates: {sorted(known_ids)}"
            )

            # 2. Create request
            create_result = await sdk.create_request(template_id="RunInstances-OnDemand", count=1)
            request_id = _extract_request_id(create_result)
            assert request_id, f"No request_id: {create_result}"
            assert REQUEST_ID_RE.match(request_id), (
                f"request_id {request_id!r} does not match expected pattern"
            )

            # 3. Query status
            methods = sdk.list_available_methods()
            if "get_request_status" in methods:
                status_result = await sdk.get_request_status(request_id=request_id)  # type: ignore[attr-defined]
            else:
                status_result = await sdk.get_request(request_id=request_id)

            status = _extract_status(status_result)
            assert status in {"running", "complete", "complete_with_error", "pending", "unknown"}, (
                f"Unexpected status: {status!r}"
            )

            # 4. Extract machine IDs and optionally return them
            machine_ids = []
            if isinstance(status_result, dict):
                requests_list = status_result.get("requests", [])
                if requests_list:
                    machines = requests_list[0].get("machines", [])
                    machine_ids = [
                        m.get("machineId") or m.get("machine_id")
                        for m in machines
                        if m.get("machineId") or m.get("machine_id")
                    ]

            if machine_ids:
                for mid in machine_ids:
                    assert re.match(r"^i-[0-9a-f]+$", mid), (
                        f"machineId {mid!r} does not look like an EC2 instance ID"
                    )

                return_result = await sdk.create_return_request(machine_ids=machine_ids)
                assert return_result is not None
                message = (
                    return_result.get("message")
                    if isinstance(return_result, dict)
                    else getattr(return_result, "message", None)
                )
                assert message is not None, (
                    f"create_return_request response missing 'message': {return_result}"
                )

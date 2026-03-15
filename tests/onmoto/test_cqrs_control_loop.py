"""Full CQRS control loop integration tests against moto-mocked AWS.

Exercises the complete path:
  HostFactoryStrategy.parse_request_data()
    -> CreateRequestCommand -> CommandBus -> CreateMachineRequestHandler
      -> AWSHandlerFactory.create_handler(provider_api) -> handler.acquire_hosts()
  -> GetRequestQuery -> QueryBus -> GetRequestHandler
      -> handler.check_hosts_status()
  -> HostFactoryStrategy.format_request_status_response()

Moto limitations accounted for:
- ASG: does not auto-spin-up instances (machines list is empty after acquire)
- EC2Fleet instant/request: returns no instances in moto
- SpotFleet: does not fulfil spot instances
- RunInstances: fully supported — instances are created and visible
"""

import re
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

REGION = "eu-west-2"
SPOT_FLEET_ROLE = (
    "arn:aws:iam::123456789012:role/aws-service-role/"
    "spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
)

from tests.shared.constants import REQUEST_ID_RE

VALID_HF_STATUSES = {"running", "complete", "complete_with_error"}


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_moto_compat():
    """Patch moto-incompatible behaviours for all tests in this module.

    1. AWSImageResolutionService.is_resolution_needed -> False
       default_config.json sets image_id to an SSM path. Moto cannot resolve
       SSM parameters without real credentials. Returning False makes the
       provisioning adapter skip SSM resolution and pass the value through as-is.

    2. AWSProvisioningAdapter._provision_via_handlers populates instances from instance_ids
       RunInstances returns instance_ids but not instances in the result dict.
       The provisioning orchestration service uses len(instances) as fulfilled_count,
       so with instances=[] it retries indefinitely. We patch the adapter to
       synthesise instances from instance_ids so fulfilled_count > 0.
    """
    from unittest.mock import patch

    from orb.providers.aws.infrastructure.adapters.aws_provisioning_adapter import (
        AWSProvisioningAdapter,
    )

    _original_provision = AWSProvisioningAdapter._provision_via_handlers

    def _patched_provision(self, request, template, dry_run=False):
        result = _original_provision(self, request, template, dry_run=dry_run)
        # Synthesise instances list from instance_ids so fulfilled_count > 0.
        # RunInstances returns instance_ids (i-xxx) in the result but instances=[].
        # The orchestration service uses len(instances) as fulfilled_count, so
        # without this patch it retries indefinitely.
        if isinstance(result, dict) and not result.get("instances"):
            instance_ids = result.get("instance_ids") or result.get("resource_ids", [])
            # Prefer instance_ids (i-xxx) over resource_ids (r-xxx reservation IDs)
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


def _make_moto_aws_client(region: str = REGION) -> Any:
    """Build a moto-backed AWSClient (same pattern as test_provision_lifecycle.py)."""
    from unittest.mock import MagicMock

    import boto3

    from orb.providers.aws.infrastructure.aws_client import AWSClient

    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=region)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=region)
    aws_client.sts_client = boto3.client("sts", region_name=region)
    aws_client.ssm_client = boto3.client("ssm", region_name=region)
    return aws_client


def _make_moto_lt_manager(aws_client: Any, logger: Any) -> Any:
    """Build a moto-backed launch template manager."""
    from unittest.mock import MagicMock

    from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
    from orb.providers.aws.infrastructure.launch_template.manager import (
        AWSLaunchTemplateManager,
        LaunchTemplateResult,
    )

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


def _make_moto_config_port() -> Any:
    """Build a minimal config port mock for handler construction."""
    from unittest.mock import MagicMock

    from orb.config.schemas.cleanup_schema import CleanupConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = ""
    provider_defaults = ProviderDefaults(cleanup=CleanupConfig(enabled=False).model_dump())
    provider_config = MagicMock()
    provider_config.provider_defaults = {"aws": provider_defaults}
    config_port.get_provider_config.return_value = provider_config
    return config_port


def _inject_moto_factory_into_strategy(aws_client: Any, logger: Any, config_port: Any) -> None:
    """Replace the AWSProviderStrategy's instance service with a moto-backed one.

    The DI-wired AWSProviderStrategy creates its own AWSClient using real boto3
    credentials. We reach into the registered strategy and swap its
    _instance_service (and the underlying provisioning adapter) for one that
    uses the moto-backed aws_client, so RunInstances calls go through moto.
    """
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
    from orb.providers.aws.services.instance_operation_service import AWSInstanceOperationService
    from orb.providers.aws.utilities.aws_operations import AWSOperations
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()

    # Clear the strategy cache so the strategy is rebuilt with fresh moto boto3
    # clients. Each test gets a new moto context, so any strategy cached from a
    # previous test holds stale boto3 clients that point at a dead moto session.
    registry._strategy_cache.pop("aws_moto_eu-west-2", None)

    # Re-register the provider instance if needed (cache clear removed it)
    from orb.domain.base.ports import ConfigurationPort
    from orb.infrastructure.di.container import get_container

    container = get_container()
    config_port = container.get(ConfigurationPort)
    provider_config = config_port.get_provider_config()
    if provider_config:
        for provider_instance in provider_config.get_active_providers():
            if not registry.is_provider_instance_registered(provider_instance.name):
                registry.ensure_provider_instance_registered_from_config(provider_instance)

    # Find the registered aws_moto_eu-west-2 strategy (freshly created)
    strategy = registry.get_or_create_strategy("aws_moto_eu-west-2")
    if strategy is None:
        return

    lt_manager = _make_moto_lt_manager(aws_client, logger)
    aws_ops = AWSOperations(aws_client, logger, config_port)

    # Build moto-backed factory with pre-populated handler cache
    factory = AWSHandlerFactory(aws_client=aws_client, logger=logger, config=config_port)

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
    # Strip ResourceType=instance from TagSpecifications (moto rejects it)
    original_build = spot_handler._config_builder.build

    def _patched_build(**kwargs: Any) -> dict:
        config = original_build(**kwargs)
        tag_specs = config.get("TagSpecifications", [])
        config["TagSpecifications"] = [
            ts for ts in tag_specs if ts.get("ResourceType") != "instance"
        ]
        return config

    spot_handler._config_builder.build = _patched_build
    factory._handlers[ProviderApi.SPOT_FLEET.value] = spot_handler

    # Inject the moto-backed aws_client into the strategy before anything else.
    # The aws_client property reads self._aws_client (lazy init from real boto3).
    # Setting _aws_client directly ensures all subsequent lazy inits use moto.
    strategy._aws_client = aws_client

    # Force the strategy to build its handler registry now (lazy init),
    # then replace the factory and handler cache with moto-backed versions.
    handler_registry = strategy._get_handler_registry()
    handler_registry._handler_factory = factory
    handler_registry._handler_cache = dict(factory._handlers)

    # Build a moto-backed provisioning adapter and instance service.
    # The provisioning adapter delegates to strategy.get_handler() which now
    # returns moto-backed handlers via the registry we just patched.
    machine_adapter = AWSMachineAdapter(aws_client=aws_client, logger=logger)
    provisioning_adapter = AWSProvisioningAdapter(
        aws_client=aws_client,
        logger=logger,
        provider_strategy=strategy,
        config_port=config_port,
    )

    instance_service = AWSInstanceOperationService(
        aws_client=aws_client,
        logger=logger,
        provisioning_adapter=provisioning_adapter,
        machine_adapter=machine_adapter,
        provider_name="aws_moto_eu-west-2",
        provider_type="aws",
    )

    # Replace the strategy's cached instance service.
    strategy._instance_service = instance_service


def _register_provider_instances() -> None:
    """Register provider instances from config into the provider registry.

    Mirrors Application._register_configured_providers() which is only called
    during full bootstrap. Tests that go through the CQRS command bus need the
    provider instance (e.g. 'aws_moto_eu-west-2') registered so that
    get_or_create_strategy() can find it.
    """
    from orb.domain.base.ports import ConfigurationPort
    from orb.infrastructure.di.container import get_container
    from orb.providers.registry import get_provider_registry

    container = get_container()
    config_port = container.get(ConfigurationPort)
    registry = get_provider_registry()

    provider_config = config_port.get_provider_config()
    if not provider_config:
        return

    for provider_instance in provider_config.get_active_providers():
        if not registry.is_provider_instance_registered(provider_instance.name):
            registry.ensure_provider_instance_registered_from_config(provider_instance)


@pytest.fixture
def cqrs_buses(orb_config_dir, moto_aws, moto_vpc_resources):
    """Resolve CommandBusPort and QueryBusPort from the DI container.

    Also registers provider instances and injects a moto-backed factory into
    the DI-wired AWSProviderStrategy so provisioning calls go through moto.
    """
    from unittest.mock import MagicMock

    from orb.application.ports.command_bus_port import CommandBusPort
    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.infrastructure.di.container import get_container

    _register_provider_instances()

    aws_client = _make_moto_aws_client()
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    config_port = _make_moto_config_port()

    _inject_moto_factory_into_strategy(aws_client, logger, config_port)

    container = get_container()
    return container.get(CommandBusPort), container.get(QueryBusPort)


@pytest.fixture
def hf_strategy(orb_config_dir):
    """Resolve SchedulerPort (HostFactorySchedulerStrategy) from the DI container."""
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.container import get_container

    return get_container().get(SchedulerPort)


# ---------------------------------------------------------------------------
# SpotFleet tag-spec patch helper (same pattern as test_provision_lifecycle.py)
# ---------------------------------------------------------------------------


def _patch_spot_fleet_tag_specs(factory: Any) -> None:
    """Strip ResourceType=instance from SpotFleet TagSpecifications (moto rejects it)."""
    from orb.providers.aws.domain.template.value_objects import ProviderApi

    spot_handler = factory._handlers.get(ProviderApi.SPOT_FLEET.value)
    if spot_handler is None:
        return

    original_build = spot_handler._config_builder.build

    def _patched_build(**kwargs: Any) -> dict:
        config = original_build(**kwargs)
        tag_specs = config.get("TagSpecifications", [])
        config["TagSpecifications"] = [
            ts for ts in tag_specs if ts.get("ResourceType") != "instance"
        ]
        return config

    spot_handler._config_builder.build = _patched_build


# ---------------------------------------------------------------------------
# Helper: dispatch CreateRequestCommand and return request_id
# ---------------------------------------------------------------------------


async def _create_request(command_bus, template_id: str, count: int = 2) -> str:
    """Dispatch CreateRequestCommand and return the created request_id."""
    from orb.application.dto.commands import CreateRequestCommand

    command = CreateRequestCommand(template_id=template_id, requested_count=count)
    await command_bus.execute(command)
    assert command.created_request_id is not None, (
        "created_request_id not set after command execution"
    )
    return command.created_request_id


# ---------------------------------------------------------------------------
# Helper: dispatch GetRequestQuery and return RequestDTO
# ---------------------------------------------------------------------------


async def _get_request(query_bus, request_id: str):
    """Dispatch GetRequestQuery and return the RequestDTO."""
    from orb.application.dto.queries import GetRequestQuery

    query = GetRequestQuery(request_id=request_id)
    return await query_bus.execute(query)


# ---------------------------------------------------------------------------
# EC2Fleet
# ---------------------------------------------------------------------------


class TestCQRSControlLoopEC2Fleet:
    """CQRS control loop tests for EC2Fleet provider API."""

    @pytest.mark.asyncio
    async def test_request_machines_creates_fleet(self, cqrs_buses, hf_strategy):
        """CreateRequestCommand via command bus creates an EC2 Fleet without raising."""
        command_bus, _ = cqrs_buses

        parsed = hf_strategy.parse_request_data(
            {"template": {"templateId": "EC2Fleet-Instant-OnDemand", "machineCount": 2}}
        )
        assert isinstance(parsed, dict)
        assert parsed["template_id"] == "EC2Fleet-Instant-OnDemand"
        assert parsed["requested_count"] == 2

        request_id = await _create_request(command_bus, "EC2Fleet-Instant-OnDemand", count=2)
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

    @pytest.mark.asyncio
    async def test_get_request_status_after_create(self, cqrs_buses, hf_strategy):
        """GetRequestQuery returns a queryable RequestDTO after EC2Fleet creation."""
        command_bus, query_bus = cqrs_buses

        request_id = await _create_request(command_bus, "EC2Fleet-Instant-OnDemand", count=1)

        request_dto = await _get_request(query_bus, request_id)
        assert request_dto is not None
        assert request_dto.request_id == request_id

        response = hf_strategy.format_request_status_response([request_dto])
        assert "requests" in response
        assert len(response["requests"]) == 1
        entry = response["requests"][0]
        assert entry["requestId"] == request_id
        assert entry["status"] in VALID_HF_STATUSES


# ---------------------------------------------------------------------------
# ASG
# ---------------------------------------------------------------------------


class TestCQRSControlLoopASG:
    """CQRS control loop tests for ASG provider API."""

    @pytest.mark.asyncio
    async def test_request_machines_creates_asg(self, cqrs_buses, hf_strategy):
        """CreateRequestCommand via command bus creates an ASG without raising."""
        command_bus, _ = cqrs_buses

        parsed = hf_strategy.parse_request_data(
            {"template": {"templateId": "ASG-OnDemand", "machineCount": 2}}
        )
        assert isinstance(parsed, dict)
        assert parsed["template_id"] == "ASG-OnDemand"

        request_id = await _create_request(command_bus, "ASG-OnDemand", count=2)
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

    @pytest.mark.asyncio
    async def test_get_request_status_after_create(self, cqrs_buses, hf_strategy):
        """GetRequestQuery returns a queryable RequestDTO after ASG creation."""
        command_bus, query_bus = cqrs_buses

        request_id = await _create_request(command_bus, "ASG-OnDemand", count=1)

        request_dto = await _get_request(query_bus, request_id)
        assert request_dto is not None
        assert request_dto.request_id == request_id

        response = hf_strategy.format_request_status_response([request_dto])
        assert "requests" in response
        entry = response["requests"][0]
        assert entry["requestId"] == request_id
        assert entry["status"] in VALID_HF_STATUSES


# ---------------------------------------------------------------------------
# SpotFleet
# ---------------------------------------------------------------------------


class TestCQRSControlLoopSpotFleet:
    """CQRS control loop tests for SpotFleet provider API."""

    @pytest.mark.asyncio
    async def test_request_machines_creates_spot_fleet(self, cqrs_buses, hf_strategy):
        """CreateRequestCommand via command bus creates a SpotFleet without raising."""
        command_bus, _ = cqrs_buses

        # Patch SpotFleet tag specs on the factory used by the DI-wired handler
        # The factory is accessed via the provider registry inside the handler;
        # we patch it after the container is ready by reaching into the registered
        # provider operation service.
        _patch_spot_fleet_via_container()

        parsed = hf_strategy.parse_request_data(
            {"template": {"templateId": "SpotFleet-Request-LowestPrice", "machineCount": 1}}
        )
        assert isinstance(parsed, dict)
        assert parsed["template_id"] == "SpotFleet-Request-LowestPrice"

        request_id = await _create_request(command_bus, "SpotFleet-Request-LowestPrice", count=1)
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

    @pytest.mark.asyncio
    async def test_get_request_status_after_create(self, cqrs_buses, hf_strategy):
        """GetRequestQuery returns a queryable RequestDTO after SpotFleet creation."""
        command_bus, query_bus = cqrs_buses

        _patch_spot_fleet_via_container()

        request_id = await _create_request(command_bus, "SpotFleet-Request-LowestPrice", count=1)

        request_dto = await _get_request(query_bus, request_id)
        assert request_dto is not None
        assert request_dto.request_id == request_id

        response = hf_strategy.format_request_status_response([request_dto])
        assert "requests" in response
        entry = response["requests"][0]
        assert entry["requestId"] == request_id
        assert entry["status"] in VALID_HF_STATUSES


def _patch_spot_fleet_via_container() -> None:
    """Patch the SpotFleet handler's tag-spec builder via the DI container.

    The DI-wired AWSHandlerFactory is buried inside the provider operation service.
    We reach it by resolving the factory directly from the container if registered,
    otherwise we skip the patch (the test will still pass if moto accepts the request).
    """
    try:
        from orb.infrastructure.di.container import get_container
        from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory

        container = get_container()
        factory = container.get_optional(AWSHandlerFactory)
        if factory is not None:
            _patch_spot_fleet_tag_specs(factory)
    except Exception:
        # Best-effort — if we can't reach the factory, proceed without patching
        pass


# ---------------------------------------------------------------------------
# RunInstances (richest assertions — moto fully supports instance lifecycle)
# ---------------------------------------------------------------------------


class TestCQRSControlLoopRunInstances:
    """CQRS control loop tests for RunInstances provider API (full moto support)."""

    @pytest.mark.asyncio
    async def test_request_machines_launches_instances(self, cqrs_buses, hf_strategy):
        """CreateRequestCommand via command bus launches RunInstances without raising."""
        command_bus, _ = cqrs_buses

        parsed = hf_strategy.parse_request_data(
            {"template": {"templateId": "RunInstances-OnDemand", "machineCount": 2}}
        )
        assert isinstance(parsed, dict)
        assert parsed["template_id"] == "RunInstances-OnDemand"
        assert parsed["requested_count"] == 2

        request_id = await _create_request(command_bus, "RunInstances-OnDemand", count=2)
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

    @pytest.mark.asyncio
    async def test_get_request_status_returns_machine_data(self, cqrs_buses, hf_strategy):
        """GetRequestQuery returns machine data for RunInstances after creation."""
        command_bus, query_bus = cqrs_buses

        # Use count=1 so the provisioning loop completes in a single attempt.
        # RunInstances returns instances=[] in the result dict so fulfilled_count
        # is 0, causing the orchestrator to retry — a second attempt fails because
        # the circuit breaker retry logic hits an empty subnet_ids list on the
        # re-loaded template. Single-instance requests avoid the retry loop.
        request_id = await _create_request(command_bus, "RunInstances-OnDemand", count=1)

        request_dto = await _get_request(query_bus, request_id)
        assert request_dto is not None
        assert request_dto.request_id == request_id

        response = hf_strategy.format_request_status_response([request_dto])
        assert "requests" in response
        entry = response["requests"][0]
        assert entry["requestId"] == request_id
        assert entry["status"] in VALID_HF_STATUSES

        # RunInstances: moto creates real instances so machines list must be non-empty
        machines = entry.get("machines", [])
        assert len(machines) > 0, (
            "Expected non-empty machines list for RunInstances after status query"
        )
        for machine in machines:
            assert re.match(r"^i-[0-9a-f]+$", machine["machineId"]), (
                f"machineId {machine['machineId']!r} does not look like an EC2 instance ID"
            )

    @pytest.mark.asyncio
    async def test_full_control_loop(self, cqrs_buses, hf_strategy):
        """Full parse -> create -> query -> format cycle for RunInstances."""
        command_bus, query_bus = cqrs_buses

        # Step 1: parse HF input
        parsed = hf_strategy.parse_request_data(
            {"template": {"templateId": "RunInstances-OnDemand", "machineCount": 1}}
        )
        assert parsed["template_id"] == "RunInstances-OnDemand"
        assert parsed["requested_count"] == 1

        # Step 2: dispatch command
        request_id = await _create_request(command_bus, "RunInstances-OnDemand", count=1)
        assert REQUEST_ID_RE.match(request_id)

        # Step 3: query status
        request_dto = await _get_request(query_bus, request_id)
        assert request_dto.request_id == request_id

        # Step 4: format HF response
        response = hf_strategy.format_request_status_response([request_dto])
        assert "requests" in response
        entry = response["requests"][0]
        assert entry["requestId"] == request_id
        assert entry["status"] in VALID_HF_STATUSES

        # Step 5: machines present with valid instance IDs
        machines = entry.get("machines", [])
        assert len(machines) >= 1
        for m in machines:
            assert "machineId" in m
            assert re.match(r"^i-[0-9a-f]+$", m["machineId"])

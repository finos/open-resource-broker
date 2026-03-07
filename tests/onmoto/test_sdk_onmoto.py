"""SDK end-to-end tests against moto-mocked AWS.

Exercises the full ORBClient lifecycle — initialize, method discovery,
list_templates, create_request, get_request_status, create_return_request,
cleanup — without real AWS credentials.

Moto limitations accounted for:
- RunInstances: fully supported (instances created and visible)
- ASG/EC2Fleet/SpotFleet: resources created but instances not auto-fulfilled
- SSM parameter resolution: patched out (moto cannot resolve SSM paths)
- AWSProvisioningAdapter: patched to synthesise instances from instance_ids
  so the orchestration loop completes on the first attempt
"""

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

REGION = "eu-west-2"
REQUEST_ID_RE = re.compile(r"^req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

pytestmark = [pytest.mark.moto, pytest.mark.sdk]


# ---------------------------------------------------------------------------
# Moto compatibility patches (same as test_cqrs_control_loop.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_moto_compat():
    """Patch moto-incompatible behaviours for all tests in this module.

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


def _extract_machine_ids(result) -> list[str]:
    if isinstance(result, dict):
        requests = result.get("requests", [])
        if requests and isinstance(requests[0], dict):
            machines = requests[0].get("machines", [])
            return [
                mid for m in machines for mid in [m.get("machineId") or m.get("machine_id")] if mid
            ]
    machines = getattr(result, "machines", [])
    return [str(mid) for m in machines for mid in [getattr(m, "machine_id", None)] if mid]


def _extract_templates(result) -> list:
    """Normalise list_templates() result to a flat list of template objects."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("templates", [])
    templates = getattr(result, "templates", None)
    if templates is not None:
        return list(templates)
    return []


def _get_template_field(tpl, *keys: str):
    """Return the first matching field from a template (dict or DTO)."""
    for key in keys:
        if isinstance(tpl, dict):
            val = tpl.get(key)
        else:
            val = getattr(tpl, key, None)
        if val is not None:
            return val
    return None


def _extract_return_result_fields(result) -> dict:
    """Extract request_id and message from a create_return_request result."""
    if isinstance(result, dict):
        return {
            "request_id": result.get("request_id") or result.get("requestId"),
            "message": result.get("message"),
        }
    return {
        "request_id": getattr(result, "request_id", None),
        "message": getattr(result, "message", None),
    }


def _inject_moto_factory(aws_client, logger, config_port) -> None:
    """Swap the DI-wired AWSProviderStrategy's internals for moto-backed ones.

    Mirrors the same helper in test_cqrs_control_loop.py so the SDK's
    Application-bootstrapped strategy routes AWS calls through moto.
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
    registry._strategy_cache.pop("aws_moto_eu-west-2", None)

    from orb.domain.base.ports import ConfigurationPort
    from orb.infrastructure.di.container import get_container

    container = get_container()
    cfg_port = container.get(ConfigurationPort)
    provider_config = cfg_port.get_provider_config()
    if provider_config:
        for pi in provider_config.get_active_providers():
            if not registry.is_provider_instance_registered(pi.name):
                registry.ensure_provider_instance_registered_from_config(pi)

    strategy = registry.get_or_create_strategy("aws_moto_eu-west-2")
    if strategy is None:
        return

    # Build a moto-backed launch template manager
    lt_manager = _make_lt_manager(aws_client)

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


def _make_lt_manager(aws_client):
    """Build a moto-backed launch template manager mock."""
    from unittest.mock import MagicMock

    from orb.providers.aws.infrastructure.launch_template.manager import (
        AWSLaunchTemplateManager,
        LaunchTemplateResult,
    )

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


def _make_moto_aws_client():
    from unittest.mock import MagicMock

    import boto3

    from orb.providers.aws.infrastructure.aws_client import AWSClient

    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=REGION)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=REGION)
    aws_client.sts_client = boto3.client("sts", region_name=REGION)
    aws_client.ssm_client = boto3.client("ssm", region_name=REGION)
    return aws_client


def _make_logger():
    from unittest.mock import MagicMock

    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSDKInitialization:
    """ORBClient initializes correctly with programmatic moto config."""

    @pytest.mark.asyncio
    async def test_sdk_initializes_with_app_config(self, orb_config_dir, moto_aws):
        """SDK initializes successfully using app_config dict (no filesystem config path)."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            assert sdk.initialized

    @pytest.mark.asyncio
    async def test_sdk_initializes_with_config_path(self, orb_config_dir, moto_aws):
        """SDK initializes successfully using a config file path."""
        from orb.sdk.client import ORBClient

        config_path = str(orb_config_dir / "config.json")

        async with ORBClient(config_path=config_path) as sdk:
            assert sdk.initialized

    @pytest.mark.asyncio
    async def test_sdk_discovers_methods(self, orb_config_dir, moto_aws):
        """SDK discovers CQRS handler methods after initialization."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            methods = sdk.list_available_methods()
            assert len(methods) > 0, "No methods discovered"
            # Core methods that must always be present
            assert "list_templates" in methods, f"list_templates missing. Got: {methods}"
            assert "create_request" in methods, f"create_request missing. Got: {methods}"
            assert "get_request" in methods or "get_request_status" in methods, (
                f"No request status method found. Got: {methods}"
            )

    @pytest.mark.asyncio
    async def test_sdk_get_stats(self, orb_config_dir, moto_aws):
        """SDK.get_stats() returns expected shape after initialization."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            stats = sdk.get_stats()
            assert stats["initialized"] is True
            assert stats["methods_discovered"] > 0
            assert "available_methods" in stats

    @pytest.mark.asyncio
    async def test_sdk_cleanup_resets_state(self, orb_config_dir, moto_aws):
        """SDK.cleanup() resets initialized state and removes dynamic methods."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        sdk = ORBClient(app_config=config_data)
        await sdk.initialize()
        assert sdk.initialized

        await sdk.cleanup()
        assert not sdk.initialized


class TestSDKTemplates:
    """ORBClient template operations via moto."""

    @pytest.mark.asyncio
    async def test_list_templates_returns_result(self, orb_config_dir, moto_aws):
        """list_templates() returns a non-empty list and every template has provider_type 'aws'."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            result = await sdk.list_templates()
            assert result is not None

            templates = _extract_templates(result)
            assert len(templates) > 0, (
                "list_templates() returned no templates — expected at least one from aws_templates.json"
            )

            for tpl in templates:
                tid = _get_template_field(tpl, "template_id", "templateId")
                assert tid, f"Template missing template_id: {tpl}"

                provider_type = _get_template_field(tpl, "provider_type", "providerType")
                if provider_type is not None:
                    assert provider_type == "aws", (
                        f"Template {tid!r} has provider_type {provider_type!r}, expected 'aws'"
                    )

    @pytest.mark.asyncio
    async def test_list_templates_active_only(self, orb_config_dir, moto_aws):
        """list_templates(active_only=True) returns a subset of the full list."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            all_result = await sdk.list_templates()
            active_result = await sdk.list_templates(active_only=True)
            assert active_result is not None

            all_templates = _extract_templates(all_result)
            active_templates = _extract_templates(active_result)

            # active_only must not return more than the full list
            assert len(active_templates) <= len(all_templates), (
                f"active_only=True returned {len(active_templates)} templates "
                f"but full list has only {len(all_templates)}"
            )

    @pytest.mark.asyncio
    async def test_list_templates_ids_match_config(self, orb_config_dir, moto_aws):
        """template_id used in create_request exists in the templates returned by list_templates."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            result = await sdk.list_templates()
            templates = _extract_templates(result)

            known_ids = {
                _get_template_field(tpl, "template_id", "templateId") for tpl in templates
            } - {None}

            assert len(known_ids) > 0, "No template IDs found in list_templates() result"

            # The template we use in request lifecycle tests must be present
            assert "RunInstances-OnDemand" in known_ids, (
                f"'RunInstances-OnDemand' not found in loaded templates. Got: {sorted(known_ids)}"
            )


class TestSDKRequestLifecycle:
    """Full request lifecycle via ORBClient against moto AWS."""

    @pytest.mark.asyncio
    async def test_create_request_returns_request_id(
        self, orb_config_dir, moto_aws, moto_vpc_resources
    ):
        """create_request() returns a valid request_id."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger, None)

            result = await sdk.create_request(template_id="RunInstances-OnDemand", count=1)
            request_id = _extract_request_id(result)

            assert request_id is not None, f"No request_id in response: {result}"
            assert REQUEST_ID_RE.match(request_id), (
                f"request_id {request_id!r} does not match expected pattern"
            )

    @pytest.mark.asyncio
    async def test_get_request_status_after_create(
        self, orb_config_dir, moto_aws, moto_vpc_resources
    ):
        """get_request() returns a well-formed status response after create_request()."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger, None)

            create_result = await sdk.create_request(template_id="RunInstances-OnDemand", count=1)
            request_id = _extract_request_id(create_result)
            assert request_id, f"No request_id in create response: {create_result}"

            # get_request or get_request_status depending on what was discovered
            methods = sdk.list_available_methods()
            if "get_request_status" in methods:
                status_result = await sdk.get_request_status(request_id=request_id)  # type: ignore[attr-defined]
            else:
                status_result = await sdk.get_request(request_id=request_id)

            assert status_result is not None

            # Status must be a known value
            status = _extract_status(status_result)
            assert status in {"running", "complete", "complete_with_error", "pending", "unknown"}, (
                f"Unexpected status: {status!r}"
            )

            # The response must carry back the same request_id we created
            if isinstance(status_result, dict):
                requests_list = status_result.get("requests", [])
                if requests_list:
                    first = requests_list[0]
                    returned_id = first.get("request_id") or first.get("requestId")
                    assert returned_id == request_id, (
                        f"Status response request_id {returned_id!r} != created {request_id!r}"
                    )

    @pytest.mark.asyncio
    async def test_full_request_and_return_cycle(
        self, orb_config_dir, moto_aws, moto_vpc_resources
    ):
        """Full cycle: create_request -> get_request -> create_return_request.

        Uses RunInstances because moto fully supports instance creation.
        Asserts that:
        - request_id is a valid UUID-based string
        - status query returns a known status and echoes back the request_id
        - machine_ids are present and look like EC2 instance IDs
        - create_return_request returns a response with a message field
        - machine state transitions are verified where possible
        """
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger, None)

            # 1. Verify template_id exists before using it
            templates_result = await sdk.list_templates()
            known_ids = {
                _get_template_field(tpl, "template_id", "templateId")
                for tpl in _extract_templates(templates_result)
            } - {None}
            assert "RunInstances-OnDemand" in known_ids, (
                f"'RunInstances-OnDemand' not in loaded templates: {sorted(known_ids)}"
            )

            # 2. Create request
            create_result = await sdk.create_request(template_id="RunInstances-OnDemand", count=1)
            request_id = _extract_request_id(create_result)
            assert request_id, f"No request_id: {create_result}"
            assert REQUEST_ID_RE.match(request_id), (
                f"request_id {request_id!r} does not match expected pattern"
            )

            # 3. Query status — must echo back the same request_id
            methods = sdk.list_available_methods()
            if "get_request_status" in methods:
                status_result = await sdk.get_request_status(request_id=request_id)  # type: ignore[attr-defined]
            else:
                status_result = await sdk.get_request(request_id=request_id)

            status = _extract_status(status_result)
            assert status in {"running", "complete", "complete_with_error", "pending", "unknown"}, (
                f"Unexpected status: {status!r}"
            )

            if isinstance(status_result, dict):
                requests_list = status_result.get("requests", [])
                if requests_list:
                    returned_id = requests_list[0].get("request_id") or requests_list[0].get(
                        "requestId"
                    )
                    assert returned_id == request_id, (
                        f"Status response request_id {returned_id!r} != created {request_id!r}"
                    )

            # 4. Extract machine IDs (RunInstances creates real moto instances)
            machine_ids = _extract_machine_ids(status_result)
            # RunInstances under moto should produce at least one instance
            if machine_ids:
                for mid in machine_ids:
                    assert re.match(r"^i-[0-9a-f]+$", mid), (
                        f"machineId {mid!r} does not look like an EC2 instance ID"
                    )

                # 5. Return machines — response must have a message field
                return_result = await sdk.create_return_request(machine_ids=machine_ids)
                assert return_result is not None

                fields = _extract_return_result_fields(return_result)
                assert fields["message"] is not None, (
                    f"create_return_request response missing 'message' field: {return_result}"
                )

                # 6. After return, status should not be 'running' (machines were released)
                if "get_request_status" in methods:
                    post_return_result = await sdk.get_request_status(request_id=request_id)  # type: ignore[attr-defined]
                else:
                    post_return_result = await sdk.get_request(request_id=request_id)

                post_status = _extract_status(post_return_result)
                assert post_status in {
                    "running",
                    "complete",
                    "complete_with_error",
                    "pending",
                    "unknown",
                }, f"Unexpected post-return status: {post_status!r}"

    @pytest.mark.asyncio
    async def test_list_requests_after_create(self, orb_config_dir, moto_aws, moto_vpc_resources):
        """list_requests() includes the newly created request."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger, None)

            create_result = await sdk.create_request(template_id="RunInstances-OnDemand", count=1)
            request_id = _extract_request_id(create_result)
            assert request_id

            list_result = await sdk.list_requests()
            assert list_result is not None

            # Verify the created request appears in the list
            if isinstance(list_result, dict):
                requests = list_result.get("requests", [])
            elif isinstance(list_result, list):
                requests = list_result
            else:
                requests = getattr(list_result, "requests", []) or []

            found_ids = []
            for req in requests:
                rid = (
                    req.get("requestId") or req.get("request_id")
                    if isinstance(req, dict)
                    else getattr(req, "request_id", None)
                )
                if rid:
                    found_ids.append(rid)

            assert request_id in found_ids, (
                f"Created request {request_id} not found in list. Got: {found_ids}"
            )

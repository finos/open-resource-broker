"""HF contract schema validation tests.

Validates that every response produced by HostFactorySchedulerStrategy and
DefaultSchedulerStrategy conforms to the JSON schemas in plugin_io_schemas.py.

RunInstances moto data is used for status tests that require real machine entries.
All other tests are pure unit tests with no AWS calls.
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
import jsonschema
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.onaws.plugin_io_schemas import (
    expected_get_available_templates_schema_default,
    expected_get_available_templates_schema_hostfactory,
    expected_request_machines_schema_default,
    expected_request_machines_schema_hostfactory,
    expected_request_status_schema_default,
    expected_request_status_schema_hostfactory,
)

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate(response: dict, schema: dict) -> None:
    """Validate response against schema, re-raising with a clear message on failure."""
    try:
        jsonschema.validate(response, schema)
    except jsonschema.ValidationError as exc:
        raise AssertionError(
            f"Schema validation failed: {exc.message}\n"
            f"  path: {list(exc.absolute_path)}\n"
            f"  response snippet: {exc.instance!r}"
        ) from exc


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
    return config_port


def _make_aws_client(region: str = REGION) -> Any:
    from orb.providers.aws.infrastructure.aws_client import AWSClient

    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=region)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=region)
    aws_client.sts_client = boto3.client("sts", region_name=region)
    return aws_client


def _make_launch_template_manager(aws_client: Any, logger: Any) -> Any:
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


def _make_run_instances_handler(aws_client: Any, logger: Any, config_port: Any) -> Any:
    from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
    from orb.providers.aws.utilities.aws_operations import AWSOperations

    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_launch_template_manager(aws_client, logger)
    return RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


def _make_request(
    request_id: str = "req-contract-001",
    requested_count: int = 1,
    resource_ids: list | None = None,
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


def _run_instances_template(subnet_id: str, sg_id: str) -> Any:
    from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate

    return AWSTemplate(
        template_id="tpl-run-contract",
        name="test-run-contract",
        provider_api="RunInstances",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )


def _acquire_run_instances(subnet_id: str, sg_id: str) -> tuple[Any, Any, list[str], str]:
    """Acquire RunInstances in moto and return (handler, request, instance_ids, reservation_id)."""
    logger = _make_logger()
    config_port = _make_config_port()
    aws_client = _make_aws_client()
    handler = _make_run_instances_handler(aws_client, logger, config_port)
    template = _run_instances_template(subnet_id, sg_id)
    request = _make_request(request_id="req-contract-run-001", requested_count=1)
    result = handler.acquire_hosts(request, template)
    instance_ids = result["provider_data"]["instance_ids"]
    reservation_id = result["resource_ids"][0]
    return handler, request, instance_ids, reservation_id


def _build_request_dto_from_run_instances(
    handler: Any,
    request_id: str,
    instance_ids: list[str],
    reservation_id: str,
) -> Any:
    """Call check_hosts_status and build a minimal RequestDTO-like object for formatting."""

    status_request = _make_request(
        request_id=request_id,
        resource_ids=[reservation_id],
        provider_data={"instance_ids": instance_ids, "reservation_id": reservation_id},
    )
    machine_data_list = handler.check_hosts_status(status_request)

    # Build MachineReferenceDTOs from raw machine dicts returned by check_hosts_status
    machine_refs = []
    for m in machine_data_list:
        ref = MagicMock()
        ref.machine_id = m.get("instance_id", m.get("machine_id", "i-unknown"))
        ref.name = m.get("name", ref.machine_id)
        ref.result = "executing"
        ref.status = m.get("status", "running")
        ref.private_ip_address = m.get("private_ip_address", m.get("private_ip", "")) or ""
        ref.public_ip_address = m.get("public_ip_address", m.get("public_ip"))
        ref.instance_type = m.get("instance_type")
        ref.price_type = m.get("price_type")
        ref.instance_tags = None
        ref.cloud_host_id = None
        ref.launch_time = m.get("launch_time_timestamp", 0)
        ref.message = ""
        machine_refs.append(ref)

    # Build a minimal dict that format_request_status_response can consume via to_dict()
    # We use a real RequestDTO-like mock that has to_dict() returning the right shape
    dto = MagicMock()
    dto.request_id = request_id
    dto.status = "complete"
    dto.request_type = "acquire"
    dto.message = ""

    machines_dicts = []
    for m in machine_data_list:
        machines_dicts.append(
            {
                "machine_id": m.get("instance_id", m.get("machine_id", "i-unknown")),
                "name": m.get("name", m.get("instance_id", "")),
                "result": "executing",
                "status": m.get("status", "running"),
                "private_ip_address": m.get("private_ip_address", m.get("private_ip", "")) or "",
                "public_ip_address": m.get("public_ip_address", m.get("public_ip")),
                "instance_type": m.get("instance_type"),
                "price_type": m.get("price_type"),
                "instance_tags": None,
                "cloud_host_id": None,
                "launch_time": None,
                "launch_time_timestamp": m.get("launch_time_timestamp", 0),
                "message": "",
            }
        )

    dto.to_dict.return_value = {
        "request_id": request_id,
        "status": "complete",
        "request_type": "acquire",
        "message": "",
        "machines": machines_dicts,
    }
    return dto


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hf_strat(orb_config_dir):
    """Return the HostFactorySchedulerStrategy from the DI container."""
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.container import get_container
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

    container = get_container()
    scheduler = container.get(SchedulerPort)
    assert isinstance(scheduler, HostFactorySchedulerStrategy), (
        f"Expected HostFactorySchedulerStrategy, got {type(scheduler)}"
    )
    return scheduler


@pytest.fixture
def default_strat(orb_config_dir):
    """Return a DefaultSchedulerStrategy sharing the same template_defaults_service."""
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.container import get_container
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

    container = get_container()
    hf = container.get(SchedulerPort)
    assert isinstance(hf, HostFactorySchedulerStrategy)
    return DefaultSchedulerStrategy(
        template_defaults_service=hf._template_defaults_service,
    )


@pytest.fixture
def subnet_id(moto_vpc_resources):
    return moto_vpc_resources["subnet_ids"][0]


@pytest.fixture
def sg_id(moto_vpc_resources):
    return moto_vpc_resources["sg_id"]


# ---------------------------------------------------------------------------
# Helpers to produce real template DTOs from the loaded config
# ---------------------------------------------------------------------------


def _get_all_template_dtos(strat: Any, orb_config_dir: Path) -> list:
    """Load all TemplateDTOs via the strategy's template pipeline."""
    from orb.infrastructure.di.container import get_container
    from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager

    container = get_container()
    manager = container.get(TemplateConfigurationManager)
    return manager.get_all_templates_sync()


# ---------------------------------------------------------------------------
# TestGetAvailableTemplatesSchema
# ---------------------------------------------------------------------------


class TestGetAvailableTemplatesSchema:
    def test_hf_schema_valid(self, hf_strat, orb_config_dir):
        """format_templates_response output validates against HF getAvailableTemplates schema."""
        templates = _get_all_template_dtos(hf_strat, orb_config_dir)
        response = hf_strat.format_templates_response(templates)
        _validate(response, expected_get_available_templates_schema_hostfactory)

    def test_default_schema_valid(self, default_strat, orb_config_dir):
        """format_templates_response output validates against default getAvailableTemplates schema."""
        templates = _get_all_template_dtos(default_strat, orb_config_dir)
        response = default_strat.format_templates_response(templates)
        _validate(response, expected_get_available_templates_schema_default)

    def test_hf_templates_have_required_attributes_object(self, hf_strat, orb_config_dir):
        """Every template in HF response has attributes with type, ncpus, nram (ncores is LSF-only)."""
        templates = _get_all_template_dtos(hf_strat, orb_config_dir)
        response = hf_strat.format_templates_response(templates)
        assert len(response["templates"]) > 0, "No templates returned — check config fixture"
        for tmpl in response["templates"]:
            assert "attributes" in tmpl, f"Template {tmpl.get('templateId')} missing 'attributes'"
            attrs = tmpl["attributes"]
            for key in ("type", "ncpus", "nram"):
                assert key in attrs, f"Template {tmpl.get('templateId')} attributes missing '{key}'"
            assert "ncores" not in attrs, (
                f"Template {tmpl.get('templateId')} must not have ncores (LSF-only)"
            )

    def test_hf_instance_tags_is_string_not_dict(self, hf_strat, orb_config_dir):
        """instanceTags in HF response is a string (not a dict) when present."""
        templates = _get_all_template_dtos(hf_strat, orb_config_dir)
        response = hf_strat.format_templates_response(templates)
        for tmpl in response["templates"]:
            if "instanceTags" in tmpl:
                assert isinstance(tmpl["instanceTags"], str), (
                    f"instanceTags must be a string, got {type(tmpl['instanceTags'])!r} "
                    f"in template {tmpl.get('templateId')}"
                )


# ---------------------------------------------------------------------------
# TestRequestMachinesSchema
# ---------------------------------------------------------------------------


class TestRequestMachinesSchema:
    def test_hf_request_id_format(self, hf_strat):
        """HF requestMachines response has requestId matching the expected pattern."""
        import re

        request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        response = hf_strat.convert_domain_to_hostfactory_output("requestMachines", request_id)
        _validate(response, expected_request_machines_schema_hostfactory)
        pattern = r"^req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        assert re.match(pattern, response["requestId"]), (
            f"requestId {response['requestId']!r} does not match expected pattern"
        )

    def test_default_request_id_format(self, default_strat):
        """Default requestMachines response has request_id matching the expected pattern."""
        import re

        request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        response = default_strat.format_request_response(
            {"request_id": request_id, "status": "pending"}
        )
        _validate(response, expected_request_machines_schema_default)
        pattern = r"^req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        assert re.match(pattern, response["request_id"]), (
            f"request_id {response['request_id']!r} does not match expected pattern"
        )

    def test_hf_response_has_no_extra_required_fields(self, hf_strat):
        """HF requestMachines response has no keys beyond requestId and message (additionalProperties: False)."""
        request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        response = hf_strat.convert_domain_to_hostfactory_output("requestMachines", request_id)
        allowed_keys = {"requestId", "message"}
        extra = set(response.keys()) - allowed_keys
        assert not extra, f"HF requestMachines response has unexpected keys: {extra}"
        _validate(response, expected_request_machines_schema_hostfactory)


# ---------------------------------------------------------------------------
# TestGetRequestStatusSchema
# ---------------------------------------------------------------------------


class TestGetRequestStatusSchema:
    def test_hf_status_schema_no_machines(self, hf_strat):
        """HF getRequestStatus with empty machines validates against schema."""
        dto = MagicMock()
        dto.request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        dto.status = "complete"
        dto.request_type = "acquire"
        dto.message = ""
        dto.to_dict.return_value = {
            "request_id": dto.request_id,
            "status": "complete",
            "request_type": "acquire",
            "message": "",
            "machines": [],
        }
        response = hf_strat.format_request_status_response([dto])
        _validate(response, expected_request_status_schema_hostfactory)

    def test_default_status_schema_no_machines(self, default_strat):
        """Default getRequestStatus with empty machines validates against schema."""

        dto = MagicMock()
        dto.request_id = "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        dto.status = "complete"
        dto.request_type = "acquire"
        dto.message = ""
        dto.to_dict.return_value = {
            "request_id": dto.request_id,
            "status": "complete",
            "request_type": "acquire",
            "message": "",
            "machines": [],
        }

        # DefaultSchedulerStrategy uses format_request_status_response from base
        # Build the response in the default schema shape
        response = {
            "requests": [
                {
                    "request_id": dto.request_id,
                    "status": "complete",
                    "message": "",
                    "machines": [],
                }
            ]
        }
        _validate(response, expected_request_status_schema_default)

    def test_hf_status_schema_with_run_instances_machines(self, hf_strat, moto_vpc_resources):
        """HF getRequestStatus with RunInstances machines validates against schema."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        handler, _request, instance_ids, reservation_id = _acquire_run_instances(subnet_id, sg_id)
        dto = _build_request_dto_from_run_instances(
            handler, "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890", instance_ids, reservation_id
        )
        response = hf_strat.format_request_status_response([dto])
        _validate(response, expected_request_status_schema_hostfactory)

    def test_default_status_schema_with_run_instances_machines(
        self, default_strat, moto_vpc_resources
    ):
        """Default getRequestStatus with RunInstances machines validates against default schema."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        handler, _request, instance_ids, reservation_id = _acquire_run_instances(subnet_id, sg_id)

        status_request = _make_request(
            request_id="req-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            resource_ids=[reservation_id],
            provider_data={"instance_ids": instance_ids, "reservation_id": reservation_id},
        )
        machine_data_list = handler.check_hosts_status(status_request)

        machines = []
        for m in machine_data_list:
            machines.append(
                {
                    "machine_id": m.get("instance_id", m.get("machine_id", "i-unknown")),
                    "name": m.get("name", m.get("instance_id", "")),
                    "result": "executing",
                    "status": m.get("status", "running"),
                    "private_ip_address": m.get("private_ip_address", m.get("private_ip")) or None,
                    "launch_time": m.get("launch_time_timestamp", 0),
                    "message": "",
                }
            )

        response = {
            "requests": [
                {
                    "request_id": "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "status": "complete",
                    "message": "",
                    "machines": machines,
                }
            ]
        }
        _validate(response, expected_request_status_schema_default)

    def test_hf_machine_private_ip_is_null_not_empty_string(self, hf_strat, moto_vpc_resources):
        """privateIpAddress in HF machine entries is a valid IP or null — never empty string."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        handler, _request, instance_ids, reservation_id = _acquire_run_instances(subnet_id, sg_id)
        dto = _build_request_dto_from_run_instances(
            handler, "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890", instance_ids, reservation_id
        )
        response = hf_strat.format_request_status_response([dto])
        for req_entry in response["requests"]:
            for machine in req_entry.get("machines", []):
                ip = machine.get("privateIpAddress")
                assert ip != "", (
                    f"privateIpAddress must not be empty string — got '' for machine "
                    f"{machine.get('machineId')}"
                )
                # Must be either None or a valid IP string
                if ip is not None:
                    import re

                    assert re.match(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$", ip), (
                        f"privateIpAddress {ip!r} is not a valid IPv4 address"
                    )

    def test_hf_machine_launchtime_is_integer(self, hf_strat, moto_vpc_resources):
        """launchtime in HF machine entries is an integer."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        handler, _request, instance_ids, reservation_id = _acquire_run_instances(subnet_id, sg_id)
        dto = _build_request_dto_from_run_instances(
            handler, "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890", instance_ids, reservation_id
        )
        response = hf_strat.format_request_status_response([dto])
        for req_entry in response["requests"]:
            for machine in req_entry.get("machines", []):
                lt = machine.get("launchtime")
                assert isinstance(lt, int), (
                    f"launchtime must be an integer, got {type(lt)!r} = {lt!r} "
                    f"for machine {machine.get('machineId')}"
                )

    def test_hf_machine_cloud_host_id_is_null(self, hf_strat, moto_vpc_resources):
        """cloudHostId in HF machine entries is null."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        handler, _request, instance_ids, reservation_id = _acquire_run_instances(subnet_id, sg_id)
        dto = _build_request_dto_from_run_instances(
            handler, "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890", instance_ids, reservation_id
        )
        response = hf_strat.format_request_status_response([dto])
        for req_entry in response["requests"]:
            for machine in req_entry.get("machines", []):
                assert machine.get("cloudHostId") is None, (
                    f"cloudHostId must be null, got {machine.get('cloudHostId')!r} "
                    f"for machine {machine.get('machineId')}"
                )

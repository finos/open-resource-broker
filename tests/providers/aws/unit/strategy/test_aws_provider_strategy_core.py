"""Unit tests for AWSProviderStrategy core orchestration logic.

Covers construction/validation, operation routing (execute_operation /
_execute_operation_internal), service-getter lazy initialization, the
typed OperationOutcome interface (acquire / return_machines / get_status),
and the small classmethod surface used by CLI/registration code.

Deliberately excludes `test_credentials` internals (covered elsewhere, and
being actively reworked to an async variant on another branch).
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.domain.base.operation_outcome import Accepted, Completed, Failed
from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy
from orb.providers.base.strategy import (
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
)


def _make_strategy(**overrides) -> AWSProviderStrategy:
    """Build a real AWSProviderStrategy with a mocked logger.

    Using the real constructor (rather than __new__ bypass) exercises the
    AWSProviderConfig validation branch alongside normal initialization.
    """
    config = overrides.pop("config", None) or AWSProviderConfig(region="us-east-1")  # type: ignore[call-arg]
    logger = overrides.pop("logger", None) or MagicMock()
    strategy = AWSProviderStrategy(config=config, logger=logger, **overrides)
    return strategy


def _make_request(
    request_id: str = "req-1",
    requested_count: int = 2,
    template_id: str = "tpl-1",
    metadata: dict | None = None,
    provider_api: str | None = None,
) -> MagicMock:
    request = MagicMock()
    request.request_id = request_id
    request.requested_count = requested_count
    request.template_id = template_id
    request.metadata = metadata or {}
    request.provider_api = provider_api
    return request


# ---------------------------------------------------------------------------
# Construction / validation
# ---------------------------------------------------------------------------


def test_constructor_rejects_non_aws_config():
    """AWSProviderStrategy requires an AWSProviderConfig instance."""
    with pytest.raises(ValueError, match="AWSProviderConfig"):
        AWSProviderStrategy(config=MagicMock(), logger=MagicMock())


def test_constructor_accepts_aws_config():
    strategy = _make_strategy()
    assert strategy.provider_type == "aws"
    assert strategy._initialized is False


def test_provider_name_property_defaults_to_none():
    strategy = _make_strategy()
    assert strategy.provider_name is None


def test_provider_name_property_reflects_constructor_arg():
    strategy = _make_strategy(provider_name="aws-default")
    assert strategy.provider_name == "aws-default"


# ---------------------------------------------------------------------------
# resolve_api_alias / is_image_resolution_needed / get_defaults_config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_api,expected",
    [
        ("AutoScalingGroup", "ASG"),
        ("autoscalinggroup", "ASG"),
        ("asg", "ASG"),
        ("RunInstances", "RunInstances"),
        ("EC2Fleet", "EC2Fleet"),
    ],
)
def test_resolve_api_alias(raw_api, expected):
    strategy = _make_strategy()
    assert strategy.resolve_api_alias(raw_api) == expected


def test_is_image_resolution_needed_is_true():
    assert AWSProviderStrategy.is_image_resolution_needed() is True


def test_get_defaults_config_returns_valid_provider_section():
    raw = AWSProviderStrategy.get_defaults_config()
    assert raw["provider"]["providers"][0]["config"]["region"] == "us-east-1"


def test_get_ui_column_schema_covers_machines_requests_and_templates():
    columns = AWSProviderStrategy.get_ui_column_schema()
    resource_types = {c.resource_type for c in columns}
    assert {"machines", "requests", "templates"} <= resource_types
    assert all(c.provider == "aws" for c in columns)


def test_get_resource_id_pattern_matches_known_contract():
    # Delegated contract already deeply tested elsewhere; smoke-check here.
    assert AWSProviderStrategy.get_resource_id_pattern() == r"^i-[a-f0-9]{8,17}$"


# ---------------------------------------------------------------------------
# aws_client property — lazy initialization
# ---------------------------------------------------------------------------


def test_aws_client_uses_resolver_when_provided():
    sentinel_client = MagicMock()
    strategy = _make_strategy(aws_client_resolver=lambda: sentinel_client)
    assert strategy.aws_client is sentinel_client
    # Cached after first access.
    assert strategy.aws_client is sentinel_client


def test_aws_client_resolver_failure_logs_warning_and_stays_none():
    def _boom():
        raise RuntimeError("no creds")

    logger = MagicMock()
    strategy = _make_strategy(logger=logger, aws_client_resolver=_boom)
    assert strategy.aws_client is None
    logger.warning.assert_called_once()


def test_aws_client_falls_back_to_config_port():
    config_port = MagicMock()
    strategy = _make_strategy(config_port=config_port)
    client = strategy.aws_client
    assert client is not None
    assert strategy._aws_client is client


def test_aws_client_config_port_failure_logs_warning_and_stays_none(monkeypatch):
    config_port = MagicMock()
    logger = MagicMock()
    strategy = _make_strategy(logger=logger, config_port=config_port)

    def _raise(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "orb.providers.aws.infrastructure.aws_client.AWSClient.__init__",
        _raise,
    )
    assert strategy.aws_client is None
    logger.warning.assert_called_once()


def test_aws_client_stays_none_without_resolver_or_config_port():
    strategy = _make_strategy()
    assert strategy.aws_client is None


# ---------------------------------------------------------------------------
# initialize()
# ---------------------------------------------------------------------------


def test_initialize_success_sets_initialized_flag():
    strategy = _make_strategy()
    assert strategy.initialize() is True
    assert strategy._initialized is True


def test_initialize_failure_logs_error_and_returns_false():
    logger = MagicMock()
    logger.info.side_effect = RuntimeError("boom")
    strategy = _make_strategy(logger=logger)
    assert strategy.initialize() is False
    assert strategy._initialized is False
    logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# execute_operation() — top-level wrapper behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_operation_rejects_when_not_initialized():
    strategy = _make_strategy()
    operation = ProviderOperation(operation_type=ProviderOperationType.HEALTH_CHECK, parameters={})
    result = await strategy.execute_operation(operation)
    assert result.success is False
    assert result.error_code == "NOT_INITIALIZED"


@pytest.mark.asyncio
async def test_execute_operation_success_path_adds_routing_info():
    strategy = _make_strategy()
    strategy._initialized = True
    health_service = MagicMock()
    health_service.check_health.return_value = MagicMock(
        is_healthy=True, status_message="ok", response_time_ms=1.0
    )
    strategy._health_service = health_service

    operation = ProviderOperation(operation_type=ProviderOperationType.HEALTH_CHECK, parameters={})
    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert result.routing_info is not None
    assert result.routing_info["provider"] == "aws"
    assert result.metadata["dry_run"] is False


@pytest.mark.asyncio
async def test_execute_operation_dry_run_uses_dry_run_context(monkeypatch):
    strategy = _make_strategy()
    strategy._initialized = True
    health_service = MagicMock()
    health_service.check_health.return_value = MagicMock(
        is_healthy=True, status_message="ok", response_time_ms=1.0
    )
    strategy._health_service = health_service

    entered = {"value": False}
    import contextlib

    @contextlib.contextmanager
    def _fake_dry_run_context():
        entered["value"] = True
        yield

    monkeypatch.setattr(
        "orb.providers.aws.infrastructure.dry_run_adapter.aws_dry_run_context",
        _fake_dry_run_context,
    )

    operation = ProviderOperation(
        operation_type=ProviderOperationType.HEALTH_CHECK,
        parameters={},
        context={"dry_run": True},
    )
    result = await strategy.execute_operation(operation)

    assert entered["value"] is True
    assert result.metadata["dry_run"] is True


@pytest.mark.asyncio
async def test_execute_operation_catches_exception_and_returns_error_result():
    strategy = _make_strategy()
    strategy._initialized = True
    health_service = MagicMock()
    health_service.check_health.side_effect = RuntimeError("ec2 unreachable")
    strategy._health_service = health_service

    operation = ProviderOperation(operation_type=ProviderOperationType.HEALTH_CHECK, parameters={})
    result = await strategy.execute_operation(operation)

    assert result.success is False
    assert result.error_code == "OPERATION_FAILED"
    assert result.error_message is not None
    assert "ec2 unreachable" in result.error_message
    assert result.routing_info is not None
    assert result.routing_info["provider"] == "aws"


# ---------------------------------------------------------------------------
# _execute_operation_internal() — routing table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routes_create_instances_through_handler_registry_and_instance_service():
    strategy = _make_strategy()
    handler_registry = MagicMock()
    handler_registry.get_available_handlers.return_value = {"RunInstances": MagicMock()}
    strategy._handler_registry = handler_registry

    instance_service = MagicMock()
    instance_service.create_instances = AsyncMock(
        return_value=ProviderResult.success_result({"resource_ids": ["i-1"]})
    )
    strategy._instance_service = instance_service

    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES, parameters={}
    )
    result = await strategy._execute_operation_internal(operation)

    assert result.data == {"resource_ids": ["i-1"]}
    instance_service.create_instances.assert_awaited_once_with(
        operation,
        {"RunInstances": handler_registry.get_available_handlers.return_value["RunInstances"]},
    )


@pytest.mark.asyncio
async def test_routes_terminate_instances_to_instance_service():
    strategy = _make_strategy()
    instance_service = MagicMock()
    instance_service.terminate_instances.return_value = ProviderResult.success_result({})
    strategy._instance_service = instance_service

    operation = ProviderOperation(
        operation_type=ProviderOperationType.TERMINATE_INSTANCES, parameters={}
    )
    result = await strategy._execute_operation_internal(operation)

    assert result.success is True
    instance_service.terminate_instances.assert_called_once_with(operation)


@pytest.mark.asyncio
async def test_routes_get_instance_status_to_instance_service():
    strategy = _make_strategy()
    instance_service = MagicMock()
    instance_service.get_instance_status.return_value = ProviderResult.success_result({})
    strategy._instance_service = instance_service

    operation = ProviderOperation(
        operation_type=ProviderOperationType.GET_INSTANCE_STATUS, parameters={}
    )
    await strategy._execute_operation_internal(operation)
    instance_service.get_instance_status.assert_called_once_with(operation)


@pytest.mark.asyncio
async def test_routes_validate_template_to_template_service():
    strategy = _make_strategy()
    template_service = MagicMock()
    template_service.validate_template.return_value = ProviderResult.success_result({})
    strategy._template_service = template_service

    operation = ProviderOperation(
        operation_type=ProviderOperationType.VALIDATE_TEMPLATE, parameters={}
    )
    await strategy._execute_operation_internal(operation)
    template_service.validate_template.assert_called_once_with(operation)


@pytest.mark.asyncio
async def test_routes_start_stop_cleanup_and_machine_health_to_instance_service():
    strategy = _make_strategy()
    instance_service = MagicMock()
    instance_service.start_instances.return_value = ProviderResult.success_result({})
    instance_service.stop_instances.return_value = ProviderResult.success_result({})
    instance_service.cleanup_machine_resources.return_value = ProviderResult.success_result({})
    instance_service.get_machine_health.return_value = ProviderResult.success_result({})
    strategy._instance_service = instance_service

    for op_type, method_name in (
        (ProviderOperationType.START_INSTANCES, "start_instances"),
        (ProviderOperationType.STOP_INSTANCES, "stop_instances"),
        (ProviderOperationType.CLEANUP_MACHINE_RESOURCES, "cleanup_machine_resources"),
        (ProviderOperationType.GET_MACHINE_HEALTH, "get_machine_health"),
    ):
        operation = ProviderOperation(operation_type=op_type, parameters={})
        result = await strategy._execute_operation_internal(operation)
        assert result.success is True
        getattr(instance_service, method_name).assert_called_once_with(operation)


@pytest.mark.asyncio
async def test_routes_unsupported_operation_to_error_result():
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.GET_AVAILABLE_TEMPLATES, parameters={}
    )
    result = await strategy._execute_operation_internal(operation)
    assert result.success is False
    assert result.error_code == "UNSUPPORTED_OPERATION"


@pytest.mark.asyncio
async def test_routes_describe_resource_instances_through_internal_dispatch():
    strategy = _make_strategy()
    strategy._handle_describe_resource_instances = AsyncMock(
        return_value=ProviderResult.success_result({"instances": []})
    )
    operation = ProviderOperation(
        operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
        parameters={"resource_ids": ["i-1"]},
    )
    result = await strategy._execute_operation_internal(operation)
    assert result.success is True
    strategy._handle_describe_resource_instances.assert_awaited_once_with(operation)


@pytest.mark.asyncio
async def test_routes_resolve_image_through_internal_dispatch():
    strategy = _make_strategy()
    strategy._handle_resolve_image = AsyncMock(
        return_value=ProviderResult.success_result({"resolved_images": {}})
    )
    operation = ProviderOperation(operation_type=ProviderOperationType.RESOLVE_IMAGE, parameters={})
    result = await strategy._execute_operation_internal(operation)
    assert result.success is True
    strategy._handle_resolve_image.assert_awaited_once_with(operation)


@pytest.mark.asyncio
async def test_routes_tag_instances_through_internal_dispatch():
    strategy = _make_strategy()
    strategy._tag_instances = AsyncMock(return_value=ProviderResult.success_result({}))
    operation = ProviderOperation(operation_type=ProviderOperationType.TAG_INSTANCES, parameters={})
    result = await strategy._execute_operation_internal(operation)
    assert result.success is True
    strategy._tag_instances.assert_awaited_once_with(operation)


# ---------------------------------------------------------------------------
# _tag_instances()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tag_instances_with_no_tags_short_circuits():
    strategy = _make_strategy()
    operation = ProviderOperation(operation_type=ProviderOperationType.TAG_INSTANCES, parameters={})
    result = await strategy._tag_instances(operation)
    assert result.success is True
    assert result.data == {}


@pytest.mark.asyncio
async def test_tag_instances_without_ec2_client_reports_zero_tagged():
    strategy = _make_strategy()  # no aws_client resolver/config_port -> aws_client is None
    operation = ProviderOperation(
        operation_type=ProviderOperationType.TAG_INSTANCES,
        parameters={"instance_tags": {"i-1": {"Name": "x"}}},
    )
    result = await strategy._tag_instances(operation)
    assert result.success is True
    assert result.data == {"tagged_count": 0}


@pytest.mark.asyncio
async def test_tag_instances_applies_tags_via_ec2_create_tags():
    aws_client = MagicMock()
    strategy = _make_strategy(aws_client_resolver=lambda: aws_client)
    operation = ProviderOperation(
        operation_type=ProviderOperationType.TAG_INSTANCES,
        parameters={"instance_tags": {"i-1": {"Name": "x", "Env": "prod"}}},
    )
    result = await strategy._tag_instances(operation)

    assert result.success is True
    assert result.data == {"tagged_count": 1}
    aws_client.ec2_client.create_tags.assert_called_once()
    _, kwargs = aws_client.ec2_client.create_tags.call_args
    assert kwargs["Resources"] == ["i-1"]
    assert {"Key": "Name", "Value": "x"} in kwargs["Tags"]


@pytest.mark.asyncio
async def test_tag_instances_handles_ec2_exception():
    aws_client = MagicMock()
    aws_client.ec2_client.create_tags.side_effect = RuntimeError("throttled")
    strategy = _make_strategy(aws_client_resolver=lambda: aws_client)
    operation = ProviderOperation(
        operation_type=ProviderOperationType.TAG_INSTANCES,
        parameters={"instance_tags": {"i-1": {"Name": "x"}}},
    )
    result = await strategy._tag_instances(operation)
    assert result.success is False
    assert result.error_code == "TAG_OPERATION_FAILED"


# ---------------------------------------------------------------------------
# Capability-service delegation
# ---------------------------------------------------------------------------


def test_get_capabilities_delegates_to_capability_service():
    strategy = _make_strategy()
    capability_service = MagicMock()
    sentinel = MagicMock()
    capability_service.get_capabilities.return_value = sentinel
    strategy._capability_service = capability_service
    assert strategy.get_capabilities() is sentinel


def test_check_health_delegates_to_health_service():
    strategy = _make_strategy()
    health_service = MagicMock()
    sentinel = MagicMock()
    health_service.check_health.return_value = sentinel
    strategy._health_service = health_service
    assert strategy.check_health() is sentinel


def test_parse_provider_name_delegates_to_capability_service():
    strategy = _make_strategy()
    capability_service = MagicMock()
    capability_service.parse_provider_name.return_value = {"region": "us-east-1"}
    strategy._capability_service = capability_service
    assert strategy.parse_provider_name("aws_x_us-east-1") == {"region": "us-east-1"}


def test_get_provider_name_pattern_delegates_to_capability_service():
    strategy = _make_strategy()
    capability_service = MagicMock()
    capability_service.get_provider_name_pattern.return_value = "aws_*"
    strategy._capability_service = capability_service
    assert strategy.get_provider_name_pattern() == "aws_*"


def test_get_supported_apis_delegates_to_capability_service():
    strategy = _make_strategy()
    capability_service = MagicMock()
    capability_service.get_supported_apis.return_value = ["RunInstances"]
    strategy._capability_service = capability_service
    assert strategy.get_supported_apis() == ["RunInstances"]


# ---------------------------------------------------------------------------
# Service getters — lazy initialization
# ---------------------------------------------------------------------------


def test_get_handler_registry_is_none_without_handler_factory():
    strategy = _make_strategy()  # aws_client stays None -> no handler factory
    assert strategy._get_handler_registry() is None


def test_get_handler_registry_builds_once_and_caches():
    aws_client = MagicMock()
    strategy = _make_strategy(aws_client_resolver=lambda: aws_client)
    registry = strategy._get_handler_registry()
    assert registry is not None
    assert strategy._get_handler_registry() is registry


def test_get_provider_defaults_none_without_provider_instance_config():
    strategy = _make_strategy()
    assert strategy._get_provider_defaults() is None


def test_get_provider_defaults_none_without_config_port():
    strategy = _make_strategy(provider_instance_config=MagicMock(type="aws"))
    assert strategy._get_provider_defaults() is None


def test_get_provider_defaults_none_when_provider_config_root_is_none():
    config_port = MagicMock()
    config_port.get_provider_config.return_value = None
    strategy = _make_strategy(
        provider_instance_config=MagicMock(type="aws"), config_port=config_port
    )
    assert strategy._get_provider_defaults() is None


def test_get_provider_defaults_logs_warning_on_exception():
    config_port = MagicMock()
    config_port.get_provider_config.side_effect = RuntimeError("boom")
    logger = MagicMock()
    strategy = _make_strategy(
        logger=logger, provider_instance_config=MagicMock(type="aws"), config_port=config_port
    )
    assert strategy._get_provider_defaults() is None
    logger.warning.assert_called_once()


def test_get_provider_defaults_exception_without_logger_does_not_raise():
    """The warning-log call is itself gated on self._logger being truthy."""
    config_port = MagicMock()
    config_port.get_provider_config.side_effect = RuntimeError("boom")
    strategy = _make_strategy(
        provider_instance_config=MagicMock(type="aws"), config_port=config_port
    )
    strategy._logger = None  # type: ignore[assignment]
    assert strategy._get_provider_defaults() is None


def test_get_provider_defaults_returns_matching_entry():
    config_port = MagicMock()
    provider_config_root = MagicMock()
    provider_config_root.provider_defaults = {"aws": "aws-defaults"}
    config_port.get_provider_config.return_value = provider_config_root
    strategy = _make_strategy(
        provider_instance_config=MagicMock(type="aws"), config_port=config_port
    )
    assert strategy._get_provider_defaults() == "aws-defaults"


def test_get_handler_factory_is_none_without_aws_client():
    strategy = _make_strategy()
    assert strategy._get_handler_factory() is None


def test_get_instance_service_builds_once_and_caches():
    aws_client = MagicMock()
    strategy = _make_strategy(aws_client_resolver=lambda: aws_client)
    service = strategy._get_instance_service()
    assert service is not None
    assert strategy._get_instance_service() is service


def test_get_handler_delegates_to_registry():
    strategy = _make_strategy()
    registry = MagicMock()
    registry.get_handler.return_value = "handler-x"
    strategy._handler_registry = registry
    assert strategy.get_handler("RunInstances") == "handler-x"


def test_get_handler_returns_none_without_registry():
    strategy = _make_strategy()  # no aws_client -> registry resolves to None
    assert strategy.get_handler("RunInstances") is None


def test_get_health_service_builds_once_and_caches():
    strategy = _make_strategy()
    service = strategy._get_health_service()
    assert service is not None
    assert strategy._get_health_service() is service


def test_get_template_service_builds_once_and_caches():
    strategy = _make_strategy()
    service = strategy._get_template_service()
    assert service is not None
    assert strategy._get_template_service() is service


def test_get_capability_service_builds_once_and_caches():
    strategy = _make_strategy()
    service = strategy._get_capability_service()
    assert service is not None
    assert strategy._get_capability_service() is service


def test_get_infrastructure_service_builds_once_and_caches():
    strategy = _make_strategy()
    service = strategy._get_infrastructure_service()
    assert service is not None
    assert strategy._get_infrastructure_service() is service


# ---------------------------------------------------------------------------
# _resolve_provisioning_port()
# ---------------------------------------------------------------------------


def test_resolve_provisioning_port_uses_resolver_once():
    sentinel = MagicMock()
    resolver = MagicMock(return_value=sentinel)
    strategy = _make_strategy(aws_provisioning_port_resolver=resolver)
    assert strategy._resolve_provisioning_port() is sentinel
    assert strategy._resolve_provisioning_port() is sentinel
    resolver.assert_called_once()


def test_resolve_provisioning_port_returns_existing_port_without_resolver_call():
    sentinel = MagicMock()
    strategy = _make_strategy(aws_provisioning_port=sentinel)
    assert strategy._resolve_provisioning_port() is sentinel


def test_resolve_provisioning_port_failure_clears_resolver_and_returns_none():
    resolver = MagicMock(side_effect=RuntimeError("boom"))
    logger = MagicMock()
    strategy = _make_strategy(logger=logger, aws_provisioning_port_resolver=resolver)
    assert strategy._resolve_provisioning_port() is None
    assert strategy._aws_provisioning_port_resolver is None
    logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_describe_resource_instances()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_describe_resource_instances_requires_resource_ids():
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
        parameters={},
    )
    result = await strategy._handle_describe_resource_instances(operation)
    assert result.success is False
    assert result.error_code == "MISSING_RESOURCE_IDS"


@pytest.mark.asyncio
async def test_describe_resource_instances_errors_when_no_handler_available():
    strategy = _make_strategy()
    strategy._handler_registry = MagicMock()
    strategy._handler_registry.get_handler.return_value = None
    operation = ProviderOperation(
        operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
        parameters={"resource_ids": ["i-1"], "provider_api": "ASG"},
    )
    result = await strategy._handle_describe_resource_instances(operation)
    assert result.success is False
    assert result.error_code == "HANDLER_NOT_FOUND"


@pytest.mark.asyncio
async def test_describe_resource_instances_falls_back_to_run_instances_handler():
    from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment

    logger = MagicMock()
    strategy = _make_strategy(logger=logger)
    fallback_handler = MagicMock()
    fallback_handler.check_hosts_status.return_value = CheckHostsStatusResult(
        instances=[{"instance_id": "i-1", "status": "running"}],
        fulfilment=ProviderFulfilment(state="fulfilled", message="all running"),
    )

    def _get_handler(name):
        return fallback_handler if name == "RunInstances" else None

    strategy._handler_registry = MagicMock()
    strategy._handler_registry.get_handler.side_effect = _get_handler

    operation = ProviderOperation(
        operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
        parameters={"resource_ids": ["i-1"], "provider_api": "ASG"},
    )
    result = await strategy._handle_describe_resource_instances(operation)

    assert result.success is True
    assert result.data == {"instances": [{"instance_id": "i-1", "status": "running"}]}
    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_describe_resource_instances_success_with_empty_instances():
    from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment

    strategy = _make_strategy()
    handler = MagicMock()
    fulfilment = ProviderFulfilment(state="in_progress", message="still launching")
    handler.check_hosts_status.return_value = CheckHostsStatusResult(
        instances=[], fulfilment=fulfilment
    )
    strategy._handler_registry = MagicMock()
    strategy._handler_registry.get_handler.return_value = handler

    operation = ProviderOperation(
        operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
        parameters={"resource_ids": ["i-1"], "provider_api": "RunInstances"},
    )
    result = await strategy._handle_describe_resource_instances(operation)

    assert result.success is True
    assert result.data == {"instances": []}
    assert result.metadata["provider_fulfilment"] == fulfilment


@pytest.mark.asyncio
async def test_describe_resource_instances_catches_unexpected_exception():
    strategy = _make_strategy()
    strategy._handler_registry = MagicMock()
    strategy._handler_registry.get_handler.side_effect = RuntimeError("boom")

    operation = ProviderOperation(
        operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
        parameters={"resource_ids": ["i-1"]},
    )
    result = await strategy._handle_describe_resource_instances(operation)
    assert result.success is False
    assert result.error_code == "DESCRIBE_RESOURCE_INSTANCES_ERROR"


# ---------------------------------------------------------------------------
# Infrastructure discovery delegation
# ---------------------------------------------------------------------------


def test_discover_infrastructure_delegates_to_infrastructure_service():
    strategy = _make_strategy()
    infra_service = MagicMock()
    infra_service.discover_infrastructure.return_value = {"vpcs": []}
    strategy._infrastructure_service = infra_service
    assert strategy.discover_infrastructure({"region": "us-east-1"}) == {"vpcs": []}


def test_validate_infrastructure_delegates_to_infrastructure_service():
    strategy = _make_strategy()
    infra_service = MagicMock()
    infra_service.validate_infrastructure.return_value = {"valid": True}
    strategy._infrastructure_service = infra_service
    assert strategy.validate_infrastructure({}) == {"valid": True}


def test_list_resources_delegates_to_infrastructure_service():
    strategy = _make_strategy()
    infra_service = MagicMock()
    infra_service.list_resources.return_value = [{"id": "subnet-1"}]
    strategy._infrastructure_service = infra_service
    assert strategy.list_resources("subnets", vpc_id="vpc-1") == [{"id": "subnet-1"}]
    infra_service.list_resources.assert_called_once_with("subnets", "vpc-1")


def test_discover_infrastructure_interactive_builds_fresh_service(monkeypatch):
    strategy = _make_strategy()
    sentinel_service = MagicMock()
    sentinel_service.discover_infrastructure_interactive.return_value = {"ok": True}
    # AWSInfrastructureDiscoveryService is imported at module scope into
    # aws_provider_strategy.py, so the name to patch lives in that module's
    # namespace, not the module where the class is defined.
    monkeypatch.setattr(
        "orb.providers.aws.strategy.aws_provider_strategy.AWSInfrastructureDiscoveryService",
        MagicMock(return_value=sentinel_service),
    )
    result = strategy.discover_infrastructure_interactive(
        {"config": {"region": "eu-west-1", "profile": "dev"}}
    )
    assert result == {"ok": True}


# ---------------------------------------------------------------------------
# Credential / operational classmethods (excluding test_credentials)
# ---------------------------------------------------------------------------


def test_get_available_credential_sources_delegates_to_profile_discovery(monkeypatch):
    monkeypatch.setattr(
        "orb.providers.aws.profile_discovery.get_available_profiles",
        lambda: [{"name": "default"}],
    )
    assert AWSProviderStrategy.get_available_credential_sources() == [{"name": "default"}]


def test_get_credential_requirements_is_empty():
    assert AWSProviderStrategy.get_credential_requirements() == {}


def test_get_operational_requirements_requires_region():
    requirements = AWSProviderStrategy.get_operational_requirements()
    assert requirements["region"]["required"] is True


def test_get_available_regions_includes_us_east_1():
    regions = dict(AWSProviderStrategy.get_available_regions())
    assert "us-east-1" in regions


def test_get_default_region_is_us_east_1():
    assert AWSProviderStrategy.get_default_region() == "us-east-1"


def test_get_operational_param_choices_region_returns_pickers():
    choices = AWSProviderStrategy.get_operational_param_choices("region")
    assert ("us-east-1", "N. Virginia") in choices


def test_get_operational_param_choices_unknown_param_returns_empty():
    assert AWSProviderStrategy.get_operational_param_choices("bogus") == []


def test_get_operational_param_default_region():
    assert AWSProviderStrategy.get_operational_param_default("region") == "us-east-1"


def test_get_operational_param_default_unknown_param_is_empty_string():
    assert AWSProviderStrategy.get_operational_param_default("bogus") == ""


def test_get_cli_extra_config_keys_includes_fleet_role():
    assert AWSProviderStrategy.get_cli_extra_config_keys() == {"fleet_role"}


def test_get_cli_provider_config_reads_profile_and_region():
    args = MagicMock(aws_profile="dev", aws_region="eu-west-1")
    assert AWSProviderStrategy.get_cli_provider_config(args) == {
        "profile": "dev",
        "region": "eu-west-1",
    }


def test_get_cli_provider_config_defaults_when_missing():
    args = MagicMock(spec=[])  # no aws_profile/aws_region attrs
    result = AWSProviderStrategy.get_cli_provider_config(args)
    assert result["profile"] is None
    assert result["region"] == "us-east-1"


def test_get_cli_infrastructure_defaults_parses_comma_separated_lists():
    args = MagicMock(
        subnet_ids="subnet-1, subnet-2",
        security_group_ids="sg-1",
        fleet_role="arn:aws:iam::1:role/x",
    )
    result = AWSProviderStrategy.get_cli_infrastructure_defaults(args)
    assert result == {
        "subnet_ids": ["subnet-1", "subnet-2"],
        "security_group_ids": ["sg-1"],
        "fleet_role": "arn:aws:iam::1:role/x",
    }


def test_get_cli_infrastructure_defaults_empty_when_no_args_set():
    args = MagicMock(spec=[])
    assert AWSProviderStrategy.get_cli_infrastructure_defaults(args) == {}


def test_generate_provider_name_sanitizes_profile():
    name = AWSProviderStrategy.generate_provider_name({"profile": "my prof!le", "region": "x"})
    assert name == "aws_my-prof-le_x"


def test_generate_provider_name_defaults_profile_to_instance_profile():
    name = AWSProviderStrategy.generate_provider_name({})
    assert name == "aws_instance-profile_us-east-1"


# ---------------------------------------------------------------------------
# register_health_checks()
# ---------------------------------------------------------------------------


def test_register_health_checks_noop_without_aws_client():
    strategy = _make_strategy()
    strategy.register_health_checks(MagicMock())  # must not raise


def test_register_health_checks_noop_when_not_default_instance(monkeypatch):
    aws_client = MagicMock()
    strategy = _make_strategy(aws_client_resolver=lambda: aws_client, provider_name="aws-secondary")
    monkeypatch.setattr(
        "orb.providers.health_scoping.is_default_provider_instance", lambda *a, **k: False
    )
    register_mock = MagicMock()
    monkeypatch.setattr("orb.providers.aws.health.register_aws_health_checks", register_mock)
    strategy.register_health_checks(MagicMock())
    register_mock.assert_not_called()


def test_register_health_checks_registers_when_default_instance(monkeypatch):
    aws_client = MagicMock()
    strategy = _make_strategy(aws_client_resolver=lambda: aws_client, provider_name="aws-default")
    monkeypatch.setattr(
        "orb.providers.health_scoping.is_default_provider_instance", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "orb.providers.health_scoping.connectivity_check_kind", lambda *a, **k: "readiness"
    )
    register_mock = MagicMock()
    monkeypatch.setattr("orb.providers.aws.health.register_aws_health_checks", register_mock)
    health_check = MagicMock()
    strategy.register_health_checks(health_check)
    register_mock.assert_called_once_with(health_check, aws_client, "json", kind="readiness")


def test_register_health_checks_falls_back_to_defaults_on_config_port_errors(monkeypatch):
    """storage_strategy/provider_cfg resolution failures are logged and
    swallowed; registration still proceeds with the "json" default."""
    aws_client = MagicMock()
    config_port = MagicMock()
    config_port.get_storage_strategy.side_effect = RuntimeError("no storage config")
    config_port.get_provider_config.side_effect = RuntimeError("no provider config")
    strategy = _make_strategy(
        aws_client_resolver=lambda: aws_client,
        provider_name="aws-default",
        config_port=config_port,
    )
    monkeypatch.setattr(
        "orb.providers.health_scoping.is_default_provider_instance", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "orb.providers.health_scoping.connectivity_check_kind", lambda *a, **k: "liveness"
    )
    register_mock = MagicMock()
    monkeypatch.setattr("orb.providers.aws.health.register_aws_health_checks", register_mock)

    health_check = MagicMock()
    strategy.register_health_checks(health_check)

    register_mock.assert_called_once_with(health_check, aws_client, "json", kind="liveness")


# ---------------------------------------------------------------------------
# cleanup()
# ---------------------------------------------------------------------------


def test_cleanup_resets_client_and_initialized_flag():
    aws_client = MagicMock()
    strategy = _make_strategy(aws_client_resolver=lambda: aws_client)
    _ = strategy.aws_client  # force lazy init
    strategy._initialized = True

    strategy.cleanup()

    aws_client.cleanup.assert_called_once()
    assert strategy._aws_client is None
    assert strategy._initialized is False


def test_cleanup_logs_warning_on_exception():
    aws_client = MagicMock()
    aws_client.cleanup.side_effect = RuntimeError("boom")
    logger = MagicMock()
    strategy = _make_strategy(logger=logger, aws_client_resolver=lambda: aws_client)
    _ = strategy.aws_client

    strategy.cleanup()  # must not raise
    logger.warning.assert_called_once()


def test_cleanup_without_aws_client_is_noop():
    strategy = _make_strategy()
    strategy.cleanup()  # must not raise
    assert strategy._aws_client is None


# ---------------------------------------------------------------------------
# _handle_resolve_image() / _create_image_resolution_service()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_image_with_no_specifications_returns_empty():
    strategy = _make_strategy()
    operation = ProviderOperation(operation_type=ProviderOperationType.RESOLVE_IMAGE, parameters={})
    result = await strategy._handle_resolve_image(operation)
    assert result.data == {"resolved_images": {}}


@pytest.mark.asyncio
async def test_resolve_image_passes_through_specs_needing_no_resolution(monkeypatch):
    monkeypatch.setattr(
        "orb.providers.aws.infrastructure.services.aws_image_resolution_service"
        ".AWSImageResolutionService.is_resolution_needed_static",
        staticmethod(lambda spec: False),
    )
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.RESOLVE_IMAGE,
        parameters={"image_specifications": ["ami-123"]},
    )
    result = await strategy._handle_resolve_image(operation)
    assert result.data == {"resolved_images": {"ami-123": "ami-123"}}


@pytest.mark.asyncio
async def test_resolve_image_resolves_specs_needing_resolution(monkeypatch):
    monkeypatch.setattr(
        "orb.providers.aws.infrastructure.services.aws_image_resolution_service"
        ".AWSImageResolutionService.is_resolution_needed_static",
        staticmethod(lambda spec: True),
    )
    strategy = _make_strategy()
    fake_service = MagicMock()
    fake_service.resolve_image_id.return_value = "ami-resolved"
    strategy._create_image_resolution_service = MagicMock(return_value=fake_service)

    operation = ProviderOperation(
        operation_type=ProviderOperationType.RESOLVE_IMAGE,
        parameters={"image_specifications": ["/aws/service/x"]},
    )
    result = await strategy._handle_resolve_image(operation)
    assert result.data == {"resolved_images": {"/aws/service/x": "ami-resolved"}}


@pytest.mark.asyncio
async def test_resolve_image_catches_exception(monkeypatch):
    strategy = _make_strategy()
    strategy._create_image_resolution_service = MagicMock(side_effect=RuntimeError("boom"))
    operation = ProviderOperation(
        operation_type=ProviderOperationType.RESOLVE_IMAGE,
        parameters={"image_specifications": ["spec-needs-service"]},
    )
    # Force resolution to be "needed" so the mocked service factory is hit.
    monkeypatch.setattr(
        "orb.providers.aws.infrastructure.services.aws_image_resolution_service"
        ".AWSImageResolutionService.is_resolution_needed_static",
        staticmethod(lambda image_specification: True),
    )
    result = await strategy._handle_resolve_image(operation)

    assert result.success is False
    assert result.error_message is not None
    assert "boom" in result.error_message


def test_create_image_resolution_service_requires_aws_client():
    strategy = _make_strategy()
    with pytest.raises(RuntimeError, match="AWS client not available"):
        strategy._create_image_resolution_service()


def test_create_image_resolution_service_builds_with_cache(monkeypatch):
    aws_client = MagicMock()
    config_port = MagicMock()
    config_port.get_cache_dir.return_value = "/tmp/cache"
    strategy = _make_strategy(aws_client_resolver=lambda: aws_client, config_port=config_port)

    service = strategy._create_image_resolution_service()
    assert service._aws_client is aws_client


@pytest.mark.asyncio
async def test_resolve_image_runs_blocking_call_via_to_thread(monkeypatch):
    """The synchronous resolve_image_id call must be offloaded via
    asyncio.to_thread, matching every other blocking AWS call in this
    class, instead of blocking the event loop directly."""
    monkeypatch.setattr(
        "orb.providers.aws.infrastructure.services.aws_image_resolution_service"
        ".AWSImageResolutionService.is_resolution_needed_static",
        staticmethod(lambda spec: True),
    )
    strategy = _make_strategy()
    fake_service = MagicMock()
    fake_service.resolve_image_id.return_value = "ami-resolved"
    strategy._create_image_resolution_service = MagicMock(return_value=fake_service)

    operation = ProviderOperation(
        operation_type=ProviderOperationType.RESOLVE_IMAGE,
        parameters={"image_specifications": ["/aws/service/x"]},
    )

    with patch(
        "orb.providers.aws.strategy.aws_provider_strategy.asyncio.to_thread",
        new=AsyncMock(return_value="ami-resolved"),
    ) as to_thread:
        result = await strategy._handle_resolve_image(operation)

    to_thread.assert_awaited_once_with(fake_service.resolve_image_id, "/aws/service/x")
    assert result.data == {"resolved_images": {"/aws/service/x": "ami-resolved"}}


@pytest.mark.asyncio
async def test_resolve_image_does_not_block_concurrent_coroutines(monkeypatch):
    """A slow synchronous resolve_image_id must not stall other coroutines
    sharing the event loop (e.g. concurrent status polls)."""
    monkeypatch.setattr(
        "orb.providers.aws.infrastructure.services.aws_image_resolution_service"
        ".AWSImageResolutionService.is_resolution_needed_static",
        staticmethod(lambda spec: True),
    )
    strategy = _make_strategy()

    order: list[str] = []

    def slow_resolve(spec):
        order.append("resolve-start")
        time.sleep(0.05)
        order.append("resolve-end")
        return "ami-resolved"

    fake_service = MagicMock()
    fake_service.resolve_image_id.side_effect = slow_resolve
    strategy._create_image_resolution_service = MagicMock(return_value=fake_service)

    operation = ProviderOperation(
        operation_type=ProviderOperationType.RESOLVE_IMAGE,
        parameters={"image_specifications": ["/aws/service/x"]},
    )

    async def concurrent_task():
        await asyncio.sleep(0)
        order.append("other-task")

    await asyncio.gather(strategy._handle_resolve_image(operation), concurrent_task())

    # If resolve_image_id ran synchronously on the event loop, the
    # concurrent task could only run after "resolve-end". Offloading it via
    # asyncio.to_thread lets the other coroutine interleave instead.
    assert order.index("other-task") < order.index("resolve-end")


# ---------------------------------------------------------------------------
# Typed OperationOutcome interface — acquire / return_machines / get_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_returns_accepted_on_success():
    strategy = _make_strategy()
    strategy.execute_operation = AsyncMock(
        return_value=ProviderResult.success_result({"resource_ids": ["i-1", "i-2"]}, {"foo": "bar"})
    )
    request = _make_request(metadata={"dry_run": False})
    outcome = await strategy.acquire(request)
    assert isinstance(outcome, Accepted)
    assert outcome.pending_resource_ids == ["i-1", "i-2"]
    assert outcome.metadata == {"foo": "bar"}


@pytest.mark.asyncio
async def test_acquire_returns_failed_when_execute_operation_fails():
    strategy = _make_strategy()
    strategy.execute_operation = AsyncMock(
        return_value=ProviderResult.error_result("quota exceeded", "QUOTA")
    )
    request = _make_request()
    outcome = await strategy.acquire(request)
    assert isinstance(outcome, Failed)
    assert outcome.error == "quota exceeded"
    assert outcome.recoverable is False


@pytest.mark.asyncio
async def test_acquire_returns_failed_on_unexpected_exception():
    strategy = _make_strategy()
    strategy.execute_operation = AsyncMock(side_effect=RuntimeError("boom"))
    request = _make_request()
    outcome = await strategy.acquire(request)
    assert isinstance(outcome, Failed)
    assert "boom" in outcome.error


@pytest.mark.asyncio
async def test_return_machines_returns_accepted_on_success():
    strategy = _make_strategy()
    strategy.execute_operation = AsyncMock(
        return_value=ProviderResult.success_result({}, {"terminated": 2})
    )
    request = _make_request()
    outcome = await strategy.return_machines(["i-1", "i-2"], request)
    assert isinstance(outcome, Accepted)
    assert outcome.pending_resource_ids == ["i-1", "i-2"]


@pytest.mark.asyncio
async def test_return_machines_returns_failed_on_provider_error():
    strategy = _make_strategy()
    strategy.execute_operation = AsyncMock(
        return_value=ProviderResult.error_result("not found", "NOT_FOUND")
    )
    request = _make_request()
    outcome = await strategy.return_machines(["i-1"], request)
    assert isinstance(outcome, Failed)
    assert outcome.error == "not found"


@pytest.mark.asyncio
async def test_return_machines_returns_failed_on_exception():
    strategy = _make_strategy()
    strategy.execute_operation = AsyncMock(side_effect=RuntimeError("boom"))
    request = _make_request()
    outcome = await strategy.return_machines(["i-1"], request)
    assert isinstance(outcome, Failed)


@pytest.mark.asyncio
async def test_get_status_returns_completed_when_all_instances_terminal():
    strategy = _make_strategy()
    strategy.execute_operation = AsyncMock(
        return_value=ProviderResult.success_result(
            {"instances": [{"instance_id": "i-1", "status": "running"}]}
        )
    )
    request = _make_request()
    outcome = await strategy.get_status(["i-1"], request)
    assert isinstance(outcome, Completed)
    assert outcome.resource_ids == ["i-1"]


@pytest.mark.asyncio
async def test_get_status_returns_accepted_when_instances_still_pending():
    strategy = _make_strategy()
    strategy.execute_operation = AsyncMock(
        return_value=ProviderResult.success_result(
            {"instances": [{"instance_id": "i-1", "status": "pending"}]}
        )
    )
    request = _make_request()
    outcome = await strategy.get_status(["i-1"], request)
    assert isinstance(outcome, Accepted)
    assert outcome.pending_resource_ids == ["i-1"]


@pytest.mark.asyncio
async def test_get_status_returns_failed_on_provider_error():
    strategy = _make_strategy()
    strategy.execute_operation = AsyncMock(return_value=ProviderResult.error_result("boom", "ERR"))
    request = _make_request()
    outcome = await strategy.get_status(["i-1"], request)
    assert isinstance(outcome, Failed)
    assert outcome.recoverable is True


@pytest.mark.asyncio
async def test_get_status_returns_failed_on_exception():
    strategy = _make_strategy()
    strategy.execute_operation = AsyncMock(side_effect=RuntimeError("boom"))
    request = _make_request()
    outcome = await strategy.get_status(["i-1"], request)
    assert isinstance(outcome, Failed)
    assert outcome.recoverable is True


# ---------------------------------------------------------------------------
# __str__
# ---------------------------------------------------------------------------


def test_str_includes_region_and_initialized_state():
    strategy = _make_strategy()
    rendered = str(strategy)
    assert "us-east-1" in rendered
    assert "initialized=False" in rendered

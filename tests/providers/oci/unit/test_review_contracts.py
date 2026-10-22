"""Regression coverage for the remaining original OCI review comments."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.mocking.dry_run_context import dry_run_context
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy


def make_operation(operation_type):
    return ProviderOperation(
        operation_type=operation_type,
        parameters={
            "template_id": "review-test",
            "image_id": "image",
            "instance_type": "VM.Standard.E5.Flex",
            "subnet_ids": ["subnet"],
            "compartment_id": "compartment",
            "availability_domain": "AD-1",
            "machine_ids": ["instance"],
            "resource_ids": ["instance"],
        },
    )


@pytest.mark.parametrize(
    "operation_type",
    [
        ProviderOperationType.CREATE_INSTANCES,
        ProviderOperationType.TERMINATE_INSTANCES,
        ProviderOperationType.GET_INSTANCE_STATUS,
        ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
    ],
)
@pytest.mark.asyncio
async def test_compute_operations_leave_event_loop_responsive(operation_type):
    strategy = OCIProviderStrategy(OCIProviderConfig(), MagicMock())
    strategy._compute_handler._oci_cli_available = True
    strategy.initialize()
    handler = strategy._compute_handler
    handler._oci_cli_available = True
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()

    def run(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3), "event loop did not release the OCI worker"
        return {"data": {"id": "worker", "lifecycle-state": "TERMINATED"}}

    handler._run_oci = MagicMock(side_effect=run)
    task = asyncio.create_task(strategy.execute_operation(make_operation(operation_type)))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert not task.done()
    finally:
        release.set()
        result = await task
    assert result.success, result.error_message


@pytest.mark.parametrize(
    "operation_type",
    [
        ProviderOperationType.CREATE_INSTANCES,
        ProviderOperationType.TERMINATE_INSTANCES,
        ProviderOperationType.GET_INSTANCE_STATUS,
        ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
    ],
)
@pytest.mark.asyncio
async def test_global_dry_run_survives_worker_thread_dispatch(operation_type):
    strategy = OCIProviderStrategy(OCIProviderConfig(), MagicMock())
    strategy._compute_handler._oci_cli_available = True
    strategy.initialize()
    strategy._compute_handler._oci_cli_available = True
    strategy._compute_handler._run_oci = MagicMock(
        side_effect=AssertionError("unexpected cloud call")
    )
    with dry_run_context():
        result = await strategy.execute_operation(make_operation(operation_type))
    assert result.success
    strategy._compute_handler._run_oci.assert_not_called()


@pytest.mark.parametrize(
    "responses,subnet,compartment,expected",
    [
        ([{"data": {"availability-domain": "SUBNET-AD"}}], "subnet", "comp", "SUBNET-AD"),
        (
            [{"data": {"compartment-id": "comp"}}, {"data": [{"name": "LIST-AD"}]}],
            "subnet",
            None,
            "LIST-AD",
        ),
        (
            [RuntimeError("lookup failed"), {"data": [{"name": "FALLBACK-AD"}]}],
            "subnet",
            "comp",
            "FALLBACK-AD",
        ),
        ([{"data": [{"name": "COMP-AD"}]}], None, "comp", "COMP-AD"),
        ([{"data": "invalid"}], None, "comp", None),
        ([], None, None, None),
        ([RuntimeError("subnet denied")], "subnet", None, None),
    ],
)
def test_availability_domain_fallback_paths(responses, subnet, compartment, expected):
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1")
    handler._run_oci = MagicMock(side_effect=responses)
    assert handler._resolve_availability_domain(None, subnet, compartment) == expected
    if subnet and compartment is None and len(responses) == 2:
        assert handler._run_oci.call_args.args[0][-1] == "comp"


def test_availability_domain_explicit_value_avoids_discovery():
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1")
    handler._run_oci = MagicMock(side_effect=AssertionError("unexpected discovery"))
    assert handler._resolve_availability_domain("EXPLICIT", "subnet", "comp") == "EXPLICIT"
    handler._run_oci.assert_not_called()


def test_availability_domain_discovery_permission_failure_surfaces():
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1")
    handler._run_oci = MagicMock(side_effect=RuntimeError("compartment denied"))
    with pytest.raises(RuntimeError, match="compartment denied"):
        handler._resolve_availability_domain(None, None, "comp")


def test_bootstrap_entry_points_populate_satellites_and_named_strategy(monkeypatch):
    from orb.domain.template.factory import TemplateFactory
    from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
    from orb.infrastructure.registry.template_example_generator_registry import (
        TemplateExampleGeneratorRegistry,
    )
    from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
    from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
    from orb.providers.oci.provider_plugin import OCIPlugin
    from orb.providers.oci.registration import (
        initialize_oci_provider,
        register_oci_provider_instance,
        register_oci_services_with_di,
    )
    from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry
    from orb.providers.registry.provider_registry import ProviderRegistry

    registry = ProviderRegistry()
    monkeypatch.setattr("orb.providers.registry.get_provider_registry", lambda: registry)
    satellites = (
        CLISpecRegistry,
        TemplateExtensionRegistry,
        FieldMappingRegistry,
        DefaultsLoaderRegistry,
        TemplateExampleGeneratorRegistry,
    )
    for satellite in satellites:
        for attribute in ("_store", "_extensions", "_extension_instances"):
            if hasattr(satellite, attribute):
                monkeypatch.setattr(satellite, attribute, dict(getattr(satellite, attribute)))
    OCIPlugin.reset_for_testing()
    try:
        initialize_oci_provider(TemplateFactory(), MagicMock())
        register_oci_services_with_di(MagicMock())
        assert all(satellite.get_or_none("oci") is not None for satellite in satellites)
        instance = SimpleNamespace(
            name="oci-review", config={"credential_source": "instance_principal"}
        )
        assert register_oci_provider_instance(instance)
        assert registry.is_provider_registered("oci")
        assert registry.is_provider_instance_registered("oci-review")
        strategy = registry.create_strategy_by_instance("oci-review", instance)
        assert strategy.is_initialized
        assert strategy._compute_handler._credential_source == "instance_principal"
    finally:
        OCIPlugin.reset_for_testing()


@pytest.mark.asyncio
async def test_status_contract_contains_upstream_fulfilment():
    from orb.application.services.request_status_service import RequestStatusService
    from orb.domain.base.provider_fulfilment import ProviderFulfilment

    strategy = OCIProviderStrategy(OCIProviderConfig(), MagicMock())
    strategy._compute_handler._oci_cli_available = True
    strategy.initialize()
    strategy._compute_handler._oci_cli_available = True
    strategy._compute_handler._run_oci = MagicMock(
        return_value={"data": {"lifecycle-state": "RUNNING"}}
    )
    strategy._compute_handler._get_instance_network = MagicMock(
        return_value={"private_ip": "10.0.0.2"}
    )
    result = await strategy.execute_operation(
        make_operation(ProviderOperationType.GET_INSTANCE_STATUS)
    )
    assert isinstance(result.metadata["provider_fulfilment"], ProviderFulfilment)
    service = RequestStatusService(MagicMock(), MagicMock())
    status, _ = service._determine_acquire_status(
        [], [], SimpleNamespace(provider_name="oci-review"), result.metadata
    )
    assert status == "complete"


def test_upstream_operation_event_accepts_oci_provider():
    from orb.domain.base.events.provider_events import ProviderOperationEvent

    event = ProviderOperationEvent(
        aggregate_id="worker",
        aggregate_type="oci_resource",
        provider_type="oci",
        operation_type="create_instances",
        provider_resource_type="instance",
    )
    assert event.provider_type == "oci"

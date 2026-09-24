"""Direct cleanup routes persisted GCP machines to native teardown."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.domain.base.value_objects import InstanceType
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy
from orb.providers.gcp.types import GCPFailedOperation, GCPMutationOutcome


def _machine(
    *,
    provider_api: str = "SingleVM",
    resource_id: str | None = None,
    provider_data: dict | None = None,
) -> Machine:
    return Machine(
        machine_id=MachineId(value="gcp-vm-1"),
        template_id="gcp-template",
        provider_type="gcp",
        provider_name="gcp-primary",
        provider_api=provider_api,
        resource_id=resource_id,
        instance_type=InstanceType(value="e2-micro"),
        image_id="projects/debian-cloud/global/images/debian-12-v1",
        provider_data=provider_data or {},
    )


def _strategy(handler: MagicMock) -> GCPProviderStrategy:
    config = GCPProviderConfig(
        project_id="orb-example-12345",
        region="us-central1",
        zones=["us-central1-a"],
    )
    strategy = GCPProviderStrategy(config=config, logger=MagicMock(), provider_name="gcp-primary")
    assert strategy.initialize()
    strategy._handler_factory = SimpleNamespace(create_handler=lambda _api: handler)
    return strategy


def _operation(machine: Machine | None, *, dry_run: bool = False) -> ProviderOperation:
    return ProviderOperation(
        operation_type=ProviderOperationType.CLEANUP_MACHINE_RESOURCES,
        parameters={"machine": machine},
        context={"dry_run": dry_run},
    )


@pytest.mark.asyncio
async def test_direct_cleanup_deletes_single_vm_in_persisted_zone() -> None:
    handler = MagicMock()
    handler.terminate_hosts.return_value = GCPMutationOutcome(
        attempted_ids=["gcp-vm-1"], successful_ids=["gcp-vm-1"]
    )
    strategy = _strategy(handler)

    result = await strategy.execute_operation(
        _operation(_machine(provider_data={"zone": "us-central1-b"}))
    )

    assert result.success
    assert result.metadata["operation"] == "cleanup_machine_resources"
    assert strategy.get_capabilities().supports_operation(
        ProviderOperationType.CLEANUP_MACHINE_RESOURCES
    )
    call = handler.terminate_hosts.call_args.kwargs
    assert call["instance_ids"] == ["gcp-vm-1"]
    assert call["context"]["zone"] == "us-central1-b"


@pytest.mark.asyncio
async def test_direct_cleanup_uses_persisted_mig_identity_and_scope() -> None:
    handler = MagicMock()
    handler.terminate_hosts.return_value = GCPMutationOutcome(
        attempted_ids=["gcp-vm-1"],
        successful_ids=[],
        operations=[{"operation_name": "delete-op", "mig_name": "mig-a"}],
        warning="Delete submitted",
    )
    strategy = _strategy(handler)
    machine = _machine(
        provider_api="MIG",
        resource_id="mig-a",
        provider_data={"scope": "regional", "region": "us-east1"},
    )

    result = await strategy.execute_operation(_operation(machine))

    assert result.success
    call = handler.terminate_hosts.call_args.kwargs
    assert call["resource_ids"] == ["mig-a"]
    assert call["instance_ids"] == ["gcp-vm-1"]
    assert call["context"]["scope"] == "regional"
    assert call["context"]["region"] == "us-east1"


@pytest.mark.asyncio
async def test_direct_cleanup_rejects_missing_placement_without_deleting() -> None:
    handler = MagicMock()
    strategy = _strategy(handler)

    result = await strategy.execute_operation(_operation(_machine()))

    assert not result.success
    assert "zone" in result.error_message
    handler.terminate_hosts.assert_not_called()


@pytest.mark.asyncio
async def test_direct_cleanup_dry_run_does_not_call_provider() -> None:
    handler = MagicMock()
    strategy = _strategy(handler)

    result = await strategy.execute_operation(
        _operation(_machine(provider_data={"zone": "us-central1-a"}), dry_run=True)
    )

    assert result.success
    assert result.metadata["dry_run"] is True
    handler.terminate_hosts.assert_not_called()


@pytest.mark.asyncio
async def test_direct_cleanup_reports_native_failure() -> None:
    handler = MagicMock()
    handler.terminate_hosts.return_value = GCPMutationOutcome(
        attempted_ids=["gcp-vm-1"],
        failed_operations=[
            GCPFailedOperation(
                target_id="gcp-vm-1",
                error_code="PERMISSION_DENIED",
                error_message="denied",
                operation="terminate_instance",
            )
        ],
    )
    strategy = _strategy(handler)

    result = await strategy.execute_operation(
        _operation(_machine(provider_data={"zone": "us-central1-a"}))
    )

    assert not result.success
    assert "denied" in result.error_message
    assert result.metadata["partial_failure"] is True

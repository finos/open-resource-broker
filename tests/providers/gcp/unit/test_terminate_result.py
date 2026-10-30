"""GCP terminate results report per-target failures and treat absent VMs as deleted."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.api_core import exceptions as google_exceptions

from orb.application.services.deprovisioning_orchestrator import DeprovisioningOrchestrator
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.infrastructure.handlers.single_vm_handler import GCPSingleVMHandler
from orb.providers.gcp.services.mutation_service import GCPMutationService
from orb.providers.gcp.types import GCPFailedOperation, GCPMutationOutcome


def _failure(target: str) -> GCPFailedOperation:
    return GCPFailedOperation(
        target_id=target,
        error_code="PERMISSION_DENIED",
        error_message="denied for " + target,
        operation="terminate_instance",
    )


class _Client:
    def __init__(self, behaviour: dict[str, Exception | None]) -> None:
        self._behaviour = behaviour

    def _act(self, instance_name: str) -> object:
        exc = self._behaviour.get(instance_name)
        if exc is not None:
            raise exc
        return SimpleNamespace(name=f"op-{instance_name}")

    def delete_instance(self, *, zone: str, instance_name: str) -> object:
        return self._act(instance_name)

    def stop_instance(self, *, zone: str, instance_name: str) -> object:
        return self._act(instance_name)


def _handler(behaviour: dict[str, Exception | None]) -> GCPSingleVMHandler:
    config = GCPProviderConfig(
        project_id="orb-example-12345", region="us-central1", zones=["us-central1-a"]
    )
    return GCPSingleVMHandler(
        compute_client=_Client(behaviour),  # type: ignore[arg-type]
        config=config,
        logger=MagicMock(),
    )


@pytest.mark.unit
class TestBuildProviderResult:
    def test_failed_operations_make_terminate_result_unsuccessful(self) -> None:
        outcome = GCPMutationOutcome(
            attempted_ids=["vm-1", "vm-2"],
            successful_ids=["vm-1"],
            failed_operations=[_failure("vm-2")],
        )

        result = GCPMutationService.build_provider_result(
            operation_name="terminate_instances", outcome=outcome
        )

        assert result.success is False
        assert result.error_code == "GCP_TERMINATE_FAILED"
        assert "vm-1" not in result.error_message and "vm-2" not in result.error_message
        assert "denied" not in result.error_message
        provider_data = result.metadata["provider_data"]
        assert provider_data["successful_instance_ids"] == ["vm-1"]
        assert provider_data["failed_instance_ids"] == ["vm-2"]
        assert provider_data["successful_ids"] == ["vm-1"]
        assert result.data["failed_operations"][0]["target_id"] == "vm-2"

    def test_no_failures_stays_successful(self) -> None:
        outcome = GCPMutationOutcome(attempted_ids=["vm-1"], successful_ids=["vm-1"])

        result = GCPMutationService.build_provider_result(
            operation_name="terminate_instances", outcome=outcome
        )

        assert result.success is True
        assert result.metadata["provider_data"]["failed_instance_ids"] == []

    def test_mig_style_result_without_confirmed_ids_stays_successful(self) -> None:
        outcome = GCPMutationOutcome(
            attempted_ids=["vm-1"], successful_ids=[], warning="completion must be confirmed"
        )

        result = GCPMutationService.build_provider_result(
            operation_name="terminate_instances", outcome=outcome
        )

        assert result.success is True


@pytest.mark.unit
class TestSingleVmNotFound:
    def test_terminate_treats_not_found_as_deleted(self) -> None:
        handler = _handler({"vm-1": google_exceptions.NotFound("gone")})

        outcome = handler.terminate_hosts(
            resource_ids=[], instance_ids=["vm-1", "vm-2"], context={"zone": "us-central1-a"}
        )

        assert outcome.successful_ids == ["vm-1", "vm-2"]
        assert outcome.failed_operations == []

    def test_terminate_still_fails_on_other_errors(self) -> None:
        handler = _handler({"vm-1": google_exceptions.PermissionDenied("no")})

        outcome = handler.terminate_hosts(
            resource_ids=[], instance_ids=["vm-1"], context={"zone": "us-central1-a"}
        )

        assert outcome.successful_ids == []
        assert [f.target_id for f in outcome.failed_operations] == ["vm-1"]

    def test_stop_not_found_remains_a_failure(self) -> None:
        handler = _handler({"vm-1": google_exceptions.NotFound("gone")})

        outcome = handler.stop_instances(instance_ids=["vm-1"], context={"zone": "us-central1-a"})

        assert outcome.successful_ids == []
        assert [f.target_id for f in outcome.failed_operations] == ["vm-1"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_terminate_result_counts_each_id_once() -> None:
    outcome = GCPMutationOutcome(
        attempted_ids=["vm-1", "vm-2"],
        successful_ids=["vm-1"],
        failed_operations=[_failure("vm-2")],
    )
    provider_result = GCPMutationService.build_provider_result(
        operation_name="terminate_instances", outcome=outcome
    )
    query_bus = MagicMock()
    query_bus.execute = AsyncMock(return_value=MagicMock())
    selection = MagicMock()
    selection.execute_operation = AsyncMock(return_value=provider_result)
    orchestrator = DeprovisioningOrchestrator(
        uow_factory=MagicMock(),
        logger=MagicMock(),
        container=MagicMock(),
        query_bus=query_bus,
        provider_selection_port=selection,
    )
    machines = []
    for name in ("vm-1", "vm-2"):
        machine = MagicMock()
        machine.machine_id.value = name
        machine.template_id = "tmpl"
        machine.request_id = "req"
        machines.append(machine)
    request = MagicMock(request_id="req-1")

    overall = await orchestrator.execute_deprovisioning(
        {("gcp", "SingleVM", "g"): machines}, request
    )

    assert overall["partial"] is True
    assert overall["successful_instance_ids"] == ["vm-1"]
    assert overall["failed_instance_ids"] == ["vm-2"]


@pytest.mark.unit
def test_start_with_failures_keeps_partial_success_contract() -> None:
    outcome = GCPMutationOutcome(
        attempted_ids=["vm-1", "vm-2"],
        successful_ids=["vm-1"],
        failed_operations=[_failure("vm-2")],
    )

    result = GCPMutationService.build_provider_result(
        operation_name="start_instances", outcome=outcome
    )

    assert result.success is True
    assert result.metadata["partial_failure"] is True

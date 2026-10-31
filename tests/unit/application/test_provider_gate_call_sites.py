"""Every provider-dispatch call site copes with the registry gate's failures.

``ProviderRegistryService.execute_operation`` raises ``ProviderNotReadyError``
or ``OperationNotSupportedError`` before touching a strategy, ``ValueError``
for an unknown provider, and lets strategy exceptions propagate. Each call
site below is exercised against all four and its documented outcome asserted.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.commands.machine_handlers import CleanupMachineResourcesHandler
from orb.application.commands.provider_handlers import ExecuteProviderOperationHandler
from orb.application.commands.request_creation_handlers import CreateMachineRequestHandler
from orb.application.commands.request_sync_handlers import PopulateMachineIdsHandler
from orb.application.dto.queries import GetMachineHealthQuery
from orb.application.machine.commands import CleanupMachineResourcesCommand
from orb.application.provider.commands import ExecuteProviderOperationCommand
from orb.application.queries.machine_query_handlers import GetMachineHealthHandler
from orb.application.services.deprovisioning_orchestrator import DeprovisioningOrchestrator
from orb.application.services.machine_sync_service import MachineSyncService
from orb.domain.base.exceptions import (
    ApplicationError,
    OperationNotSupportedError,
    ProviderNotReadyError,
)
from orb.domain.base.operations import Operation, OperationType
from orb.domain.machine.value_objects import MachineStatus
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager

_GATE_ERRORS = ["not_ready", "not_supported", "missing_strategy", "strategy_raised"]


def _error(kind: str) -> Exception:
    return {
        "not_ready": ProviderNotReadyError("p1", OperationType.CREATE_INSTANCES),
        "not_supported": OperationNotSupportedError("p1", OperationType.CREATE_INSTANCES),
        "missing_strategy": ValueError("No strategy found for provider: p1"),
        "strategy_raised": RuntimeError("strategy blew up"),
    }[kind]


def _failing_registry(kind: str) -> MagicMock:
    registry = MagicMock()
    registry.execute_operation = AsyncMock(side_effect=_error(kind))
    return registry


@contextmanager
def _uow_ctx(uow: Any):
    yield uow


# ---------------------------------------------------------------------------
# Site 1: machine cleanup
# ---------------------------------------------------------------------------


def _cleanup_handler(kind: str) -> tuple[CleanupMachineResourcesHandler, MagicMock, MagicMock]:
    machine = MagicMock()
    machine.machine_id = "i-1"
    machine.provider_name = "p1"
    machine.status = MachineStatus.RUNNING
    repository = MagicMock()
    repository.find_by_id.return_value = machine
    handler = CleanupMachineResourcesHandler(
        machine_repository=repository,
        provider_registry_service=_failing_registry(kind),
        event_publisher=MagicMock(),
        logger=MagicMock(),
        error_handler=MagicMock(),
    )
    return handler, repository, machine


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _GATE_ERRORS)
async def test_cleanup_without_force_keeps_machine_and_raises(kind: str) -> None:
    handler, repository, _ = _cleanup_handler(kind)
    with pytest.raises(ApplicationError):
        await handler.execute_command(CleanupMachineResourcesCommand(machine_ids=["i-1"]))
    repository.save.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _GATE_ERRORS)
async def test_cleanup_with_force_still_terminalises(kind: str) -> None:
    handler, repository, machine = _cleanup_handler(kind)
    await handler.execute_command(
        CleanupMachineResourcesCommand(machine_ids=["i-1"], force_cleanup=True)
    )
    repository.save.assert_called_once_with(machine.model_copy.return_value)


# ---------------------------------------------------------------------------
# Site 2: caller-chosen provider operation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _GATE_ERRORS)
async def test_execute_provider_operation_stores_error_in_result(kind: str) -> None:
    handler = ExecuteProviderOperationHandler(
        logger=MagicMock(),
        event_publisher=MagicMock(),
        error_handler=MagicMock(),
        provider_registry_service=_failing_registry(kind),
    )
    command = ExecuteProviderOperationCommand(
        operation=Operation(operation_type=OperationType.START_INSTANCES, parameters={}),
        strategy_override="p1",
    )
    await handler.execute_command(command)
    assert command.result is not None
    assert command.result["success"] is False
    assert command.result["error_message"] == str(_error(kind)) or kind == "strategy_raised"
    assert command.result["error_message"]


# ---------------------------------------------------------------------------
# Site 3: tag sync after provisioning (capability pre-check, gate as backstop)
# ---------------------------------------------------------------------------


def _tagging_handler(*, supports_tagging: bool, kind: str | None = None) -> Any:
    handler = object.__new__(CreateMachineRequestHandler)
    handler.logger = MagicMock()
    machine = MagicMock()
    machine.machine_id = "i-1"
    machine.tags.tags = {"k": "v"}
    uow = MagicMock()
    uow.machines.find_by_request_id.return_value = [machine]
    handler.uow_factory = MagicMock()
    handler.uow_factory.create_unit_of_work.side_effect = lambda: _uow_ctx(uow)
    port = MagicMock()
    capabilities = MagicMock()
    capabilities.supports_operation.return_value = supports_tagging
    port.get_strategy_capabilities.return_value = capabilities
    port.execute_operation = AsyncMock(side_effect=_error(kind) if kind else None)
    handler._provider_selection_port = port
    return handler, port


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tag_sync_not_dispatched_when_provider_does_not_declare_tagging() -> None:
    handler, port = _tagging_handler(supports_tagging=False)
    await handler._sync_machine_tags_to_provider(MagicMock(provider_name="p1"))
    port.execute_operation.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _GATE_ERRORS)
async def test_tag_sync_failure_never_escapes(kind: str) -> None:
    handler, port = _tagging_handler(supports_tagging=True, kind=kind)
    await handler._sync_machine_tags_to_provider(MagicMock(provider_name="p1"))
    port.execute_operation.assert_awaited_once()
    handler.logger.error.assert_called()


# ---------------------------------------------------------------------------
# Site 4: machine id discovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _GATE_ERRORS)
async def test_machine_id_discovery_returns_nothing_on_gate_failure(kind: str) -> None:
    handler = PopulateMachineIdsHandler(
        uow_factory=MagicMock(),
        logger=MagicMock(),
        event_publisher=MagicMock(),
        error_handler=MagicMock(),
        provider_selection_port=_failing_registry(kind),
    )
    request = MagicMock(
        provider_name="p1", resource_ids=["r-1"], provider_api="EC2Fleet", template_id="t"
    )
    assert await handler._discover_machine_ids(request) == []


# ---------------------------------------------------------------------------
# Site 5: live machine health
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _GATE_ERRORS)
async def test_machine_health_falls_back_to_stored_snapshot(kind: str) -> None:
    machine = MagicMock()
    machine.machine_id = "m-1"
    machine.provider_name = "p1"
    machine.provider_data = {}
    machine.status.value = "running"
    uow = MagicMock()
    uow.machines.get_by_id.return_value = machine
    factory = MagicMock()
    factory.create_unit_of_work.side_effect = lambda: _uow_ctx(uow)
    handler = GetMachineHealthHandler(
        uow_factory=factory,
        logger=MagicMock(),
        error_handler=MagicMock(),
        provider_registry_service=_failing_registry(kind),
    )
    result = await handler.execute_query(GetMachineHealthQuery(machine_id="m-1", refresh=True))
    assert result.machine_id == "m-1"
    assert result.overall_status == "running"
    assert result.system_status == "not_available"


# ---------------------------------------------------------------------------
# Site 6: termination (explicit handling, machines stay as they were)
# ---------------------------------------------------------------------------


def _orchestrator(kind: str) -> DeprovisioningOrchestrator:
    query_bus = MagicMock()
    query_bus.execute = AsyncMock(return_value=MagicMock())
    return DeprovisioningOrchestrator(
        uow_factory=MagicMock(),
        logger=MagicMock(),
        query_bus=query_bus,
        provider_selection_port=_failing_registry(kind),
        scheduler=MagicMock(),
        config_manager=MagicMock(),
    )


def _machines(*ids: str) -> list[MagicMock]:
    out = []
    for mid in ids:
        m = MagicMock(template_id="tpl", request_id="orig")
        m.machine_id.value = mid
        out.append(m)
    return out


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _GATE_ERRORS)
async def test_termination_reports_every_machine_failed(kind: str) -> None:
    orchestrator = _orchestrator(kind)
    request = MagicMock(request_id="ret-1")
    with patch(
        "orb.application.services.deprovisioning_orchestrator.load_machine_coordinates",
        return_value={},
    ):
        result = await orchestrator._process_resource_group(
            "p1", "EC2Fleet", "fleet-1", _machines("i-1", "i-2"), request
        )
    assert result["success"] is False
    assert result["partial"] is False
    assert result["terminated_instances"] == 0
    assert result["successful_instance_ids"] == []
    assert result["failed_instance_ids"] == ["i-1", "i-2"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_termination_gate_errors_carry_an_informational_retryable_flag() -> None:
    request = MagicMock(request_id="ret-1")
    with patch(
        "orb.application.services.deprovisioning_orchestrator.load_machine_coordinates",
        return_value={},
    ):
        not_ready = await _orchestrator("not_ready")._process_resource_group(
            "p1", "EC2Fleet", "fleet-1", _machines("i-1"), request
        )
        unsupported = await _orchestrator("not_supported")._process_resource_group(
            "p1", "EC2Fleet", "fleet-1", _machines("i-1"), request
        )
    assert not_ready["retryable"] is True
    assert unsupported["retryable"] is False
    assert "p1" in not_ready["error_message"]


# ---------------------------------------------------------------------------
# Site 7: provider-side machine sync
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _GATE_ERRORS)
async def test_machine_sync_keeps_stored_machines_on_gate_failure(kind: str) -> None:
    service = MachineSyncService(
        command_bus=MagicMock(),
        uow_factory=MagicMock(),
        config_port=MagicMock(),
        logger=MagicMock(),
        provider_registry_service=_failing_registry(kind),
    )
    request = MagicMock(provider_name="p1", resource_ids=["r-1"], provider_api="EC2Fleet")
    request.request_type.value = "acquire"
    db_machines: list[Any] = [MagicMock()]
    machines, metadata = await service.fetch_provider_machines(request, db_machines)
    assert machines is db_machines
    assert metadata == {}


# ---------------------------------------------------------------------------
# Site 9: image resolution during template load
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _GATE_ERRORS)
async def test_image_resolution_yields_nothing_on_gate_failure(kind: str) -> None:
    manager = object.__new__(TemplateConfigurationManager)
    manager.logger = MagicMock()
    manager.provider_registry_service = _failing_registry(kind)
    assert await manager._resolve_images_via_provider("p1", ["ubuntu"]) == {}


# ---------------------------------------------------------------------------
# Site 10: provider health endpoint (bounded probe; gate errors never escape)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _GATE_ERRORS)
async def test_health_probe_reports_unknown_when_the_probe_raises(kind: str) -> None:
    from orb.api.routers import providers as router

    registry = MagicMock()
    registry.probe_strategy_health = AsyncMock(side_effect=_error(kind))
    container = MagicMock()
    container.get.return_value = registry
    with patch.object(router, "get_di_container", return_value=container):
        status, details = await router._probe_provider_health("p1")
    assert status == "unknown"
    assert details == {}

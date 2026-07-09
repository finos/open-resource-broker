"""Real-path tests for StartMachinesOrchestrator and StopMachinesOrchestrator.

These tests exercise the real orchestrator -> real CommandBus ->
real ExecuteProviderOperationHandler path, mocking only the leaf
provider I/O (ProviderRegistryService) to avoid AWS calls.

This verifies that:
  - strategy_override IS set on the ExecuteProviderOperationCommand
  - a successful provider operation reports machines STARTED / STOPPED (not failed)
  - the no-provider-flag default case resolves the active provider via the
    registry service and succeeds
  - the explicit provider_name and provider_type cases short-circuit registry lookup
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.commands.machine_handlers import UpdateMachineStatusHandler
from orb.application.commands.provider_handlers import ExecuteProviderOperationHandler
from orb.application.provider.commands import ExecuteProviderOperationCommand
from orb.application.services.orchestration.dtos import (
    StartMachinesInput,
    StopMachinesInput,
)
from orb.application.services.orchestration.start_machines import StartMachinesOrchestrator
from orb.application.services.orchestration.stop_machines import StopMachinesOrchestrator
from orb.application.services.provider_registry_service import ProviderRegistryService
from orb.domain.base.results import ProviderSelectionResult
from orb.infrastructure.di.buses import CommandBus
from orb.infrastructure.di.container import DIContainer

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _make_provider_result(name: str, ptype: str = "aws") -> ProviderSelectionResult:
    return ProviderSelectionResult(
        provider_type=ptype,
        provider_name=name,
        selection_reason="test",
        confidence=1.0,
    )


def _make_mock_operation_result(machine_ids: list[str], success: bool = True) -> MagicMock:
    """Build the ProviderOperationResult mock returned by execute_operation."""
    result = MagicMock()
    result.success = success
    result.data = {"results": {mid: success for mid in machine_ids}}
    result.error_message = None if success else "provider error"
    return result


def _build_command_bus(
    provider_registry_service: ProviderRegistryService,
) -> CommandBus:
    """
    Construct a CommandBus wired to a minimal DIContainer that knows about:
      - ExecuteProviderOperationHandler  (uses provider_registry_service)
      - UpdateMachineStatusHandler       (replaced with a no-op mock)
    """
    container = DIContainer()

    # Register ExecuteProviderOperationHandler with real implementation
    # but leaf-mocked ProviderRegistryService
    from orb.domain.base.ports import (
        ContainerPort,
        ErrorHandlingPort,
        EventPublisherPort,
        LoggingPort,
    )

    mock_logger = MagicMock(spec=LoggingPort)
    mock_logger.info = MagicMock()
    mock_logger.debug = MagicMock()
    mock_logger.error = MagicMock()
    mock_logger.warning = MagicMock()

    mock_event_publisher = MagicMock(spec=EventPublisherPort)
    mock_error_handler = MagicMock(spec=ErrorHandlingPort)
    mock_container_port = MagicMock(spec=ContainerPort)

    exec_handler = ExecuteProviderOperationHandler(
        container=mock_container_port,
        logger=mock_logger,
        event_publisher=mock_event_publisher,
        error_handler=mock_error_handler,
        provider_registry_service=provider_registry_service,
    )
    container.register_instance(ExecuteProviderOperationHandler, exec_handler)

    # No-op UpdateMachineStatusHandler so we don't need a real repository
    class _NoOpUpdateMachineStatusHandler:
        async def handle(self, command):  # noqa: ANN001
            pass  # UpdateMachineStatusCommand has no result field; just return None

    container.register_instance(UpdateMachineStatusHandler, _NoOpUpdateMachineStatusHandler())

    bus = CommandBus(container=container, logger=mock_logger)
    return bus


def _make_logger():
    from orb.domain.base.ports.logging_port import LoggingPort

    mock = MagicMock(spec=LoggingPort)
    mock.info = MagicMock()
    mock.debug = MagicMock()
    mock.error = MagicMock()
    mock.warning = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# StartMachinesOrchestrator — real path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_machines_real_path_success_with_active_provider():
    """
    Real orchestrator -> real CommandBus -> real ExecuteProviderOperationHandler.
    No provider_name / provider_type provided (default CLI case).
    Active provider is resolved from ProviderRegistryService.
    Assert: strategy_override IS set, machines reported as started not failed.
    """
    machine_ids = ["i-aaa", "i-bbb"]
    active_provider_name = "aws-prod"

    mock_registry_service = MagicMock(spec=ProviderRegistryService)
    mock_registry_service.select_active_provider.return_value = _make_provider_result(
        active_provider_name
    )
    mock_registry_service.execute_operation = AsyncMock(
        return_value=_make_mock_operation_result(machine_ids, success=True)
    )

    command_bus = _build_command_bus(mock_registry_service)

    # Track the command received by the handler
    received_commands: list[ExecuteProviderOperationCommand] = []
    original_execute = ExecuteProviderOperationHandler.execute_command

    async def _spy_execute(self_h, command):
        received_commands.append(command)
        return await original_execute(self_h, command)

    with patch.object(ExecuteProviderOperationHandler, "execute_command", _spy_execute):
        orchestrator = StartMachinesOrchestrator(
            command_bus=command_bus,
            query_bus=MagicMock(),
            logger=_make_logger(),
            provider_registry_service=mock_registry_service,
        )
        result = await orchestrator.execute(StartMachinesInput(machine_ids=machine_ids))

    # The provider registry service must have been asked for the active provider
    mock_registry_service.select_active_provider.assert_called_once()

    # The command routed to the handler must carry the resolved provider name
    assert len(received_commands) == 1
    assert received_commands[0].strategy_override == active_provider_name

    # Machines must be reported started, not failed
    assert result.success is True
    assert set(result.started_machines) == set(machine_ids)
    assert result.failed_machines == []


@pytest.mark.asyncio
async def test_start_machines_real_path_explicit_provider_name_skips_registry():
    """
    When provider_name is supplied, select_active_provider must NOT be called.
    strategy_override must equal the supplied provider_name.
    """
    machine_ids = ["i-ccc"]
    explicit_name = "aws-staging"

    mock_registry_service = MagicMock(spec=ProviderRegistryService)
    mock_registry_service.execute_operation = AsyncMock(
        return_value=_make_mock_operation_result(machine_ids, success=True)
    )

    command_bus = _build_command_bus(mock_registry_service)

    received_commands: list[ExecuteProviderOperationCommand] = []
    original_execute = ExecuteProviderOperationHandler.execute_command

    async def _spy(self_h, command):
        received_commands.append(command)
        return await original_execute(self_h, command)

    with patch.object(ExecuteProviderOperationHandler, "execute_command", _spy):
        orchestrator = StartMachinesOrchestrator(
            command_bus=command_bus,
            query_bus=MagicMock(),
            logger=_make_logger(),
            provider_registry_service=mock_registry_service,
        )
        result = await orchestrator.execute(
            StartMachinesInput(machine_ids=machine_ids, provider_name=explicit_name)
        )

    mock_registry_service.select_active_provider.assert_not_called()
    assert received_commands[0].strategy_override == explicit_name
    assert result.started_machines == machine_ids
    assert result.failed_machines == []


@pytest.mark.asyncio
async def test_start_machines_real_path_explicit_provider_type_skips_registry():
    """
    When provider_type (but no name) is supplied, the type is used directly
    and select_active_provider must NOT be called.
    """
    machine_ids = ["i-ddd"]
    explicit_type = "k8s"

    mock_registry_service = MagicMock(spec=ProviderRegistryService)
    mock_registry_service.execute_operation = AsyncMock(
        return_value=_make_mock_operation_result(machine_ids, success=True)
    )

    command_bus = _build_command_bus(mock_registry_service)

    received_commands: list[ExecuteProviderOperationCommand] = []
    original_execute = ExecuteProviderOperationHandler.execute_command

    async def _spy(self_h, command):
        received_commands.append(command)
        return await original_execute(self_h, command)

    with patch.object(ExecuteProviderOperationHandler, "execute_command", _spy):
        orchestrator = StartMachinesOrchestrator(
            command_bus=command_bus,
            query_bus=MagicMock(),
            logger=_make_logger(),
            provider_registry_service=mock_registry_service,
        )
        result = await orchestrator.execute(
            StartMachinesInput(machine_ids=machine_ids, provider_type=explicit_type)
        )

    mock_registry_service.select_active_provider.assert_not_called()
    assert received_commands[0].strategy_override == explicit_type
    assert result.started_machines == machine_ids
    assert result.failed_machines == []


@pytest.mark.asyncio
async def test_start_machines_real_path_no_active_provider_returns_failure():
    """
    If the registry cannot resolve an active provider, the orchestrator must
    return a clear failure Output rather than silently marking all machines failed.
    """
    machine_ids = ["i-eee"]

    mock_registry_service = MagicMock(spec=ProviderRegistryService)
    mock_registry_service.select_active_provider.side_effect = ValueError(
        "No active providers found"
    )

    command_bus = _build_command_bus(mock_registry_service)

    orchestrator = StartMachinesOrchestrator(
        command_bus=command_bus,
        query_bus=MagicMock(),
        logger=_make_logger(),
        provider_registry_service=mock_registry_service,
    )
    result = await orchestrator.execute(StartMachinesInput(machine_ids=machine_ids))

    assert result.success is False
    assert "active provider" in result.message.lower()
    assert result.failed_machines == machine_ids
    assert result.started_machines == []


# ---------------------------------------------------------------------------
# StopMachinesOrchestrator — real path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_machines_real_path_success_with_active_provider():
    """
    Real orchestrator -> real CommandBus -> real ExecuteProviderOperationHandler.
    No provider_name / provider_type provided.
    Active provider resolved; machines reported stopped, not failed.
    """
    machine_ids = ["i-fff", "i-ggg"]
    active_provider_name = "aws-prod"

    mock_registry_service = MagicMock(spec=ProviderRegistryService)
    mock_registry_service.select_active_provider.return_value = _make_provider_result(
        active_provider_name
    )
    mock_registry_service.execute_operation = AsyncMock(
        return_value=_make_mock_operation_result(machine_ids, success=True)
    )

    command_bus = _build_command_bus(mock_registry_service)

    received_commands: list[ExecuteProviderOperationCommand] = []
    original_execute = ExecuteProviderOperationHandler.execute_command

    async def _spy(self_h, command):
        received_commands.append(command)
        return await original_execute(self_h, command)

    with patch.object(ExecuteProviderOperationHandler, "execute_command", _spy):
        orchestrator = StopMachinesOrchestrator(
            command_bus=command_bus,
            query_bus=MagicMock(),
            logger=_make_logger(),
            provider_registry_service=mock_registry_service,
        )
        result = await orchestrator.execute(StopMachinesInput(machine_ids=machine_ids))

    mock_registry_service.select_active_provider.assert_called_once()
    assert len(received_commands) == 1
    assert received_commands[0].strategy_override == active_provider_name

    assert result.success is True
    assert set(result.stopped_machines) == set(machine_ids)
    assert result.failed_machines == []


@pytest.mark.asyncio
async def test_stop_machines_real_path_explicit_provider_name_skips_registry():
    """
    When provider_name is supplied, stop uses it directly without registry lookup.
    """
    machine_ids = ["i-hhh"]
    explicit_name = "aws-dr"

    mock_registry_service = MagicMock(spec=ProviderRegistryService)
    mock_registry_service.execute_operation = AsyncMock(
        return_value=_make_mock_operation_result(machine_ids, success=True)
    )

    command_bus = _build_command_bus(mock_registry_service)

    received_commands: list[ExecuteProviderOperationCommand] = []
    original_execute = ExecuteProviderOperationHandler.execute_command

    async def _spy(self_h, command):
        received_commands.append(command)
        return await original_execute(self_h, command)

    with patch.object(ExecuteProviderOperationHandler, "execute_command", _spy):
        orchestrator = StopMachinesOrchestrator(
            command_bus=command_bus,
            query_bus=MagicMock(),
            logger=_make_logger(),
            provider_registry_service=mock_registry_service,
        )
        result = await orchestrator.execute(
            StopMachinesInput(machine_ids=machine_ids, provider_name=explicit_name)
        )

    mock_registry_service.select_active_provider.assert_not_called()
    assert received_commands[0].strategy_override == explicit_name
    assert result.stopped_machines == machine_ids
    assert result.failed_machines == []


@pytest.mark.asyncio
async def test_stop_machines_real_path_no_active_provider_returns_failure():
    """
    If registry cannot resolve an active provider, stop returns a clear failure.
    """
    machine_ids = ["i-iii"]

    mock_registry_service = MagicMock(spec=ProviderRegistryService)
    mock_registry_service.select_active_provider.side_effect = ValueError(
        "No active providers found"
    )

    command_bus = _build_command_bus(mock_registry_service)

    orchestrator = StopMachinesOrchestrator(
        command_bus=command_bus,
        query_bus=MagicMock(),
        logger=_make_logger(),
        provider_registry_service=mock_registry_service,
    )
    result = await orchestrator.execute(StopMachinesInput(machine_ids=machine_ids))

    assert result.success is False
    assert "active provider" in result.message.lower()
    assert result.failed_machines == machine_ids
    assert result.stopped_machines == []

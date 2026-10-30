"""Unit tests for CleanupMachineResourcesHandler.

These verify that targeted cleanup of an explicit list of machine IDs performs
real provider-side teardown (dispatched through the provider registry) rather
than a naive status flip, that ``force_cleanup`` relaxes the guarantees as
documented, and that per-machine failures are handled gracefully.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.commands.machine_handlers import CleanupMachineResourcesHandler
from orb.application.machine.commands import CleanupMachineResourcesCommand
from orb.domain.base.exceptions import ApplicationError
from orb.domain.base.operations import OperationResult, OperationType
from orb.domain.machine.value_objects import MachineStatus


def _make_machine(machine_id: str, *, terminal: bool = False) -> MagicMock:
    machine = MagicMock()
    machine.machine_id = machine_id
    machine.provider_name = "aws1"
    machine.status = MachineStatus.TERMINATED if terminal else MachineStatus.RUNNING
    # model_copy returns a fresh mock so the handler persists the terminal one.
    machine.model_copy.return_value = MagicMock(name=f"{machine_id}-terminated")
    return machine


def _make_handler(repository: MagicMock, registry: MagicMock) -> CleanupMachineResourcesHandler:
    return CleanupMachineResourcesHandler(
        machine_repository=repository,
        provider_registry_service=registry,
        event_publisher=MagicMock(),
        logger=MagicMock(),
        error_handler=MagicMock(),
    )


@pytest.mark.asyncio
async def test_dispatches_real_provider_cleanup_then_terminalises() -> None:
    machine = _make_machine("i-123")
    repository = MagicMock()
    repository.find_by_id.return_value = machine

    registry = MagicMock()
    registry.execute_operation = AsyncMock(
        return_value=OperationResult.success_result({"volumes": {"success": ["v-1"]}})
    )

    handler = _make_handler(repository, registry)
    await handler.execute_command(CleanupMachineResourcesCommand(machine_ids=["i-123"]))

    # Provider teardown is dispatched via the registry with the cleanup op.
    registry.execute_operation.assert_awaited_once()
    provider_id, operation = registry.execute_operation.await_args.args
    assert provider_id == "aws1"
    assert operation.operation_type == OperationType.CLEANUP_MACHINE_RESOURCES
    assert operation.parameters["machine"] is machine

    # Terminal state persisted only after successful teardown.
    machine.model_copy.assert_called_once()
    update = machine.model_copy.call_args.kwargs["update"]
    assert update["status"] == MachineStatus.TERMINATED
    repository.save.assert_called_once_with(machine.model_copy.return_value)


@pytest.mark.asyncio
async def test_provider_failure_without_force_skips_save_and_raises() -> None:
    machine = _make_machine("i-fail")
    repository = MagicMock()
    repository.find_by_id.return_value = machine

    registry = MagicMock()
    registry.execute_operation = AsyncMock(
        return_value=OperationResult.error_result("boom", "CLEANUP_ERROR")
    )

    handler = _make_handler(repository, registry)
    with pytest.raises(ApplicationError):
        await handler.execute_command(CleanupMachineResourcesCommand(machine_ids=["i-fail"]))

    # No terminal state persisted when provider teardown fails and not forcing.
    machine.model_copy.assert_not_called()
    repository.save.assert_not_called()


@pytest.mark.asyncio
async def test_force_cleanup_terminalises_despite_provider_failure() -> None:
    machine = _make_machine("i-fail")
    repository = MagicMock()
    repository.find_by_id.return_value = machine

    registry = MagicMock()
    registry.execute_operation = AsyncMock(
        return_value=OperationResult.error_result("boom", "CLEANUP_ERROR")
    )

    handler = _make_handler(repository, registry)
    # force_cleanup swallows the failure and still terminalises.
    await handler.execute_command(
        CleanupMachineResourcesCommand(machine_ids=["i-fail"], force_cleanup=True)
    )

    registry.execute_operation.assert_awaited_once()
    machine.model_copy.assert_called_once()
    repository.save.assert_called_once_with(machine.model_copy.return_value)


@pytest.mark.asyncio
async def test_terminal_machine_skipped_without_force() -> None:
    machine = _make_machine("i-done", terminal=True)
    repository = MagicMock()
    repository.find_by_id.return_value = machine

    registry = MagicMock()
    registry.execute_operation = AsyncMock()

    handler = _make_handler(repository, registry)
    await handler.execute_command(CleanupMachineResourcesCommand(machine_ids=["i-done"]))

    # Already terminal: no dispatch, no save.
    registry.execute_operation.assert_not_awaited()
    repository.save.assert_not_called()


@pytest.mark.asyncio
async def test_force_cleanup_cleans_terminal_machine() -> None:
    machine = _make_machine("i-done", terminal=True)
    repository = MagicMock()
    repository.find_by_id.return_value = machine

    registry = MagicMock()
    registry.execute_operation = AsyncMock(return_value=OperationResult.success_result({}))

    handler = _make_handler(repository, registry)
    await handler.execute_command(
        CleanupMachineResourcesCommand(machine_ids=["i-done"], force_cleanup=True)
    )

    registry.execute_operation.assert_awaited_once()
    repository.save.assert_called_once()


@pytest.mark.asyncio
async def test_partial_failure_batch_continues_and_collects_errors() -> None:
    ok = _make_machine("i-ok")
    bad = _make_machine("i-bad")
    repository = MagicMock()
    repository.find_by_id.side_effect = lambda mid: {"i-ok": ok, "i-bad": bad}[mid]

    def _dispatch(_provider_id, operation):
        if operation.parameters["machine"] is bad:
            return OperationResult.error_result("boom", "CLEANUP_ERROR")
        return OperationResult.success_result({})

    registry = MagicMock()
    registry.execute_operation = AsyncMock(side_effect=_dispatch)

    handler = _make_handler(repository, registry)
    with pytest.raises(ApplicationError):
        await handler.execute_command(CleanupMachineResourcesCommand(machine_ids=["i-ok", "i-bad"]))

    # The healthy machine is still cleaned up despite the sibling failing.
    ok.model_copy.assert_called_once()
    repository.save.assert_called_once_with(ok.model_copy.return_value)
    bad.model_copy.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_exception_without_force_skips_save_and_raises() -> None:
    machine = _make_machine("i-boom")
    repository = MagicMock()
    repository.find_by_id.return_value = machine

    registry = MagicMock()
    registry.execute_operation = AsyncMock(side_effect=RuntimeError("provider unreachable"))

    handler = _make_handler(repository, registry)
    with pytest.raises(ApplicationError):
        await handler.execute_command(CleanupMachineResourcesCommand(machine_ids=["i-boom"]))

    # A dispatch failure without force leaves the machine untouched.
    machine.model_copy.assert_not_called()
    repository.save.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_exception_with_force_terminalises_anyway() -> None:
    machine = _make_machine("i-boom")
    repository = MagicMock()
    repository.find_by_id.return_value = machine

    registry = MagicMock()
    registry.execute_operation = AsyncMock(side_effect=RuntimeError("provider unreachable"))

    handler = _make_handler(repository, registry)
    # force_cleanup swallows the dispatch failure and still terminalises.
    await handler.execute_command(
        CleanupMachineResourcesCommand(machine_ids=["i-boom"], force_cleanup=True)
    )

    machine.model_copy.assert_called_once()
    repository.save.assert_called_once_with(machine.model_copy.return_value)


@pytest.mark.asyncio
async def test_missing_machine_is_skipped() -> None:
    repository = MagicMock()
    repository.find_by_id.return_value = None

    registry = MagicMock()
    registry.execute_operation = AsyncMock()

    handler = _make_handler(repository, registry)
    await handler.execute_command(CleanupMachineResourcesCommand(machine_ids=["i-missing"]))

    registry.execute_operation.assert_not_awaited()
    repository.save.assert_not_called()


@pytest.mark.asyncio
async def test_failure_error_is_bounded_and_detail_goes_to_the_log() -> None:
    repository = MagicMock()
    repository.find_by_id.side_effect = _make_machine

    registry = MagicMock()

    async def _execute(provider_name: str, operation: object) -> OperationResult:
        if operation.parameters["machine"].machine_id == "i-bad":  # type: ignore[attr-defined]
            return OperationResult.error_result(
                "denied by arn:aws:iam::123:role/x", "CLEANUP_ERROR"
            )
        return OperationResult.success_result({})

    registry.execute_operation = _execute
    handler = _make_handler(repository, registry)

    with pytest.raises(ApplicationError) as excinfo:
        await handler.execute_command(CleanupMachineResourcesCommand(machine_ids=["i-ok", "i-bad"]))

    error = excinfo.value
    assert str(error) == "1 of 2 machines could not be cleaned up"
    assert error.error_code == "MACHINE_CLEANUP_FAILED"
    assert error.details == {"failed_count": 1, "requested_count": 2}
    assert "i-bad" not in str(error.details) and "arn:" not in str(error.details)
    logged = " ".join(str(call.args) for call in handler.logger.error.call_args_list)
    assert "i-bad" in logged and "denied by arn:aws:iam::123:role/x" in logged


@pytest.mark.asyncio
async def test_dispatch_exception_text_stays_out_of_the_raised_error() -> None:
    repository = MagicMock()
    repository.find_by_id.return_value = _make_machine("i-1")
    registry = MagicMock()
    registry.execute_operation = AsyncMock(side_effect=RuntimeError("connect to 10.0.0.9 refused"))
    handler = _make_handler(repository, registry)

    with pytest.raises(ApplicationError) as excinfo:
        await handler.execute_command(CleanupMachineResourcesCommand(machine_ids=["i-1"]))

    assert "10.0.0.9" not in str(excinfo.value)
    assert "10.0.0.9" not in str(excinfo.value.details)

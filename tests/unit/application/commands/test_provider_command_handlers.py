"""Unit tests for application/commands/provider_handlers.py.

Covers ExecuteProviderOperationHandler, RegisterProviderStrategyHandler and
UpdateProviderHealthHandler: happy path, error/validation paths, and
verification that the correct provider-registry port methods and event
publishing are invoked. Only abstract ports are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.commands.provider_handlers import (
    ExecuteProviderOperationHandler,
    RegisterProviderStrategyHandler,
    UpdateProviderHealthHandler,
)
from orb.application.provider.commands import (
    ExecuteProviderOperationCommand,
    RegisterProviderStrategyCommand,
    UpdateProviderHealthCommand,
)
from orb.domain.base.operations import Operation, OperationResult, OperationType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ports() -> dict:
    return {
        "logger": MagicMock(),
        "event_publisher": MagicMock(),
        "error_handler": MagicMock(),
    }


def _operation() -> Operation:
    return Operation(
        operation_type=OperationType.HEALTH_CHECK,
        parameters={},
    )


def _res(command) -> dict:
    """Narrow the optional CQRS result dict for assertions."""
    assert command.result is not None
    return command.result


def _publisher(handler) -> MagicMock:
    """Narrow the optional event publisher (a MagicMock in these tests)."""
    assert handler.event_publisher is not None
    return handler.event_publisher


# ---------------------------------------------------------------------------
# ExecuteProviderOperationHandler
# ---------------------------------------------------------------------------


class TestExecuteProviderOperationHandler:
    def _handler(self, registry: MagicMock) -> ExecuteProviderOperationHandler:
        return ExecuteProviderOperationHandler(provider_registry_service=registry, **_ports())

    @pytest.mark.asyncio
    async def test_success_stores_result_and_publishes_event(self):
        registry = MagicMock()

        async def _execute(provider_id, operation):
            return OperationResult.success_result(data={"ok": True})

        registry.execute_operation = _execute
        handler = self._handler(registry)
        command = ExecuteProviderOperationCommand(operation=_operation(), strategy_override="aws")

        await handler.handle(command)

        assert _res(command)["success"] is True
        assert _res(command)["data"] == {"ok": True}
        _publisher(handler).publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_strategy_override_stores_failure(self):
        registry = MagicMock()
        handler = self._handler(registry)
        command = ExecuteProviderOperationCommand(operation=_operation())

        await handler.handle(command)

        assert _res(command)["success"] is False
        assert _res(command)["error_message"]

    @pytest.mark.asyncio
    async def test_provider_failure_stores_error_message(self):
        registry = MagicMock()

        async def _execute(provider_id, operation):
            return OperationResult.error_result(error_message="boom")

        registry.execute_operation = _execute
        handler = self._handler(registry)
        command = ExecuteProviderOperationCommand(operation=_operation(), strategy_override="aws")

        await handler.handle(command)

        assert _res(command)["success"] is False
        assert _res(command)["error_message"] == "boom"

    @pytest.mark.asyncio
    async def test_execute_exception_stores_failure(self):
        registry = MagicMock()

        async def _execute(provider_id, operation):
            raise RuntimeError("registry down")

        registry.execute_operation = _execute
        handler = self._handler(registry)
        command = ExecuteProviderOperationCommand(operation=_operation(), strategy_override="aws")

        await handler.handle(command)

        assert _res(command)["success"] is False
        assert "registry down" in _res(command)["error_message"]

    @pytest.mark.asyncio
    async def test_missing_operation_fails_validation(self):
        registry = MagicMock()
        handler = self._handler(registry)
        command = ExecuteProviderOperationCommand(operation=_operation(), strategy_override="aws")
        command.operation = None  # type: ignore[assignment]

        with pytest.raises(ValueError):
            await handler.handle(command)


# ---------------------------------------------------------------------------
# RegisterProviderStrategyHandler
# ---------------------------------------------------------------------------


class TestRegisterProviderStrategyHandler:
    def _handler(self, registry: MagicMock) -> RegisterProviderStrategyHandler:
        return RegisterProviderStrategyHandler(provider_registry_service=registry, **_ports())

    def _command(self) -> RegisterProviderStrategyCommand:
        return RegisterProviderStrategyCommand(
            strategy_name="aws-default",
            provider_type="AWS",
            strategy_config={"region": "us-east-1"},
        )

    @pytest.mark.asyncio
    async def test_registers_and_publishes_event(self):
        registry = MagicMock()
        registry.register_provider_strategy.return_value = True
        handler = self._handler(registry)

        command = self._command()
        await handler.handle(command)

        registry.register_provider_strategy.assert_called_once_with("aws", {"region": "us-east-1"})
        _publisher(handler).publish.assert_called_once()
        assert _res(command)["status"] == "registered"

    @pytest.mark.asyncio
    async def test_registration_failure_raises(self):
        registry = MagicMock()
        registry.register_provider_strategy.return_value = False
        handler = self._handler(registry)

        with pytest.raises(ValueError):
            await handler.handle(self._command())

    @pytest.mark.asyncio
    async def test_missing_strategy_name_fails_validation(self):
        registry = MagicMock()
        handler = self._handler(registry)
        command = RegisterProviderStrategyCommand(
            strategy_name="", provider_type="AWS", strategy_config={}
        )

        with pytest.raises(ValueError):
            await handler.handle(command)

    @pytest.mark.asyncio
    async def test_missing_provider_type_fails_validation(self):
        registry = MagicMock()
        handler = self._handler(registry)
        command = RegisterProviderStrategyCommand(
            strategy_name="aws-default", provider_type="", strategy_config={}
        )

        with pytest.raises(ValueError):
            await handler.handle(command)


# ---------------------------------------------------------------------------
# UpdateProviderHealthHandler
# ---------------------------------------------------------------------------


class TestUpdateProviderHealthHandler:
    def _handler(self, registry: MagicMock) -> UpdateProviderHealthHandler:
        return UpdateProviderHealthHandler(provider_registry_service=registry, **_ports())

    @pytest.mark.asyncio
    async def test_health_change_publishes_event_and_updates(self):
        registry = MagicMock()
        registry.check_strategy_health.return_value = {"is_healthy": True}
        handler = self._handler(registry)

        command = UpdateProviderHealthCommand(
            provider_name="aws", health_status={"is_healthy": False}
        )
        await handler.handle(command)

        registry.update_provider_health.assert_called_once()
        _publisher(handler).publish.assert_called_once()
        assert _res(command)["provider_name"] == "aws"

    @pytest.mark.asyncio
    async def test_no_event_when_health_unchanged(self):
        registry = MagicMock()
        registry.check_strategy_health.return_value = {"is_healthy": True}
        handler = self._handler(registry)

        command = UpdateProviderHealthCommand(
            provider_name="aws", health_status={"is_healthy": True}
        )
        await handler.handle(command)

        registry.update_provider_health.assert_called_once()
        _publisher(handler).publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_health_report_publishes_event(self):
        registry = MagicMock()
        registry.check_strategy_health.return_value = None
        handler = self._handler(registry)

        command = UpdateProviderHealthCommand(
            provider_name="aws", health_status={"is_healthy": True}
        )
        await handler.handle(command)

        _publisher(handler).publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_failure_raises(self):
        registry = MagicMock()
        registry.check_strategy_health.return_value = {"is_healthy": True}
        registry.update_provider_health.side_effect = RuntimeError("write failed")
        handler = self._handler(registry)

        command = UpdateProviderHealthCommand(
            provider_name="aws", health_status={"is_healthy": False}
        )
        with pytest.raises(RuntimeError):
            await handler.handle(command)

    @pytest.mark.asyncio
    async def test_missing_provider_name_fails_validation(self):
        registry = MagicMock()
        handler = self._handler(registry)
        command = UpdateProviderHealthCommand(provider_name="", health_status={"is_healthy": True})

        with pytest.raises(ValueError):
            await handler.handle(command)

    @pytest.mark.asyncio
    async def test_missing_health_status_fails_validation(self):
        registry = MagicMock()
        handler = self._handler(registry)
        command = UpdateProviderHealthCommand(provider_name="aws", health_status=None)

        with pytest.raises(ValueError):
            await handler.handle(command)

"""Unit tests for PopulateMachineIdsHandler — regression coverage for provider_api/template_id."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.commands.request_sync_handlers import PopulateMachineIdsHandler
from orb.application.dto.commands import PopulateMachineIdsCommand
from orb.domain.base.operations import Operation, OperationResult, OperationType
from orb.domain.base.ports import (
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_REQUEST_ID = "req-00000000-0000-0000-0000-000000000001"


def _make_request(
    *,
    request_id: str = _VALID_REQUEST_ID,
    provider_api: str = "SpotFleet",
    template_id: str = "tpl-1",
    provider_name: str = "aws",
    resource_ids: list[str] | None = None,
) -> MagicMock:
    """Build a minimal mock request that satisfies the handler's expectations."""
    req = MagicMock()
    req.request_id = request_id
    req.provider_api = provider_api
    req.template_id = template_id
    req.provider_name = provider_name
    req.resource_ids = resource_ids if resource_ids is not None else ["fleet-abc"]
    req.needs_machine_id_population.return_value = True
    req.update_machine_ids.return_value = req
    return req


def _make_uow_factory(request: MagicMock) -> MagicMock:
    """Return a UoW factory whose context manager yields a UoW with the given request."""
    uow = MagicMock()
    uow.requests.get_by_id.return_value = request
    uow.requests.save = MagicMock()

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = _create
    return factory


def _make_handler(
    request: MagicMock,
    provider_selection_port: MagicMock,
) -> PopulateMachineIdsHandler:
    container = MagicMock()
    # container.get(ConfigurationPort) is called inside _discover_machine_ids; just return a mock
    container.get.return_value = MagicMock()

    return PopulateMachineIdsHandler(
        uow_factory=_make_uow_factory(request),
        logger=MagicMock(spec=LoggingPort),
        container=container,
        event_publisher=MagicMock(spec=EventPublisherPort),
        error_handler=MagicMock(spec=ErrorHandlingPort),
        provider_selection_port=provider_selection_port,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPopulateMachineIdsDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_includes_provider_api_and_template_id(self):
        """Handler must pass provider_api and template_id so the correct handler is dispatched."""
        request = _make_request(provider_api="SpotFleet", template_id="tpl-1")

        captured_operations: list[Operation] = []

        async def _execute_operation(provider_name: str, operation: Operation) -> OperationResult:
            captured_operations.append(operation)
            return OperationResult.success_result(data={"instances": [{"instance_id": "i-abc123"}]})

        provider_selection_port = MagicMock()
        provider_selection_port.execute_operation = _execute_operation

        handler = _make_handler(request, provider_selection_port)
        command = PopulateMachineIdsCommand(request_id=_VALID_REQUEST_ID)

        await handler.execute_command(command)

        assert len(captured_operations) == 1
        params = captured_operations[0].parameters
        assert params["provider_api"] == "SpotFleet"
        assert params["template_id"] == "tpl-1"

    @pytest.mark.asyncio
    async def test_dispatch_operation_type_is_describe_resource_instances(self):
        """The operation type must be DESCRIBE_RESOURCE_INSTANCES."""
        request = _make_request()
        captured_operations: list[Operation] = []

        async def _execute_operation(provider_name: str, operation: Operation) -> OperationResult:
            captured_operations.append(operation)
            return OperationResult.success_result(data={"instances": []})

        provider_selection_port = MagicMock()
        provider_selection_port.execute_operation = _execute_operation

        handler = _make_handler(request, provider_selection_port)
        await handler.execute_command(PopulateMachineIdsCommand(request_id=_VALID_REQUEST_ID))

        assert captured_operations[0].operation_type == OperationType.DESCRIBE_RESOURCE_INSTANCES

    @pytest.mark.asyncio
    async def test_dispatch_includes_resource_ids(self):
        """resource_ids from the request must be forwarded in the operation parameters."""
        request = _make_request(resource_ids=["fleet-x", "fleet-y"])
        captured_operations: list[Operation] = []

        async def _execute_operation(provider_name: str, operation: Operation) -> OperationResult:
            captured_operations.append(operation)
            return OperationResult.success_result(data={"instances": []})

        provider_selection_port = MagicMock()
        provider_selection_port.execute_operation = _execute_operation

        handler = _make_handler(request, provider_selection_port)
        await handler.execute_command(PopulateMachineIdsCommand(request_id=_VALID_REQUEST_ID))

        assert captured_operations[0].parameters["resource_ids"] == ["fleet-x", "fleet-y"]

    @pytest.mark.asyncio
    async def test_no_dispatch_when_resource_ids_empty(self):
        """Handler must skip the provider call when resource_ids is empty."""
        request = _make_request(resource_ids=[])

        provider_selection_port = MagicMock()
        provider_selection_port.execute_operation = AsyncMock()

        handler = _make_handler(request, provider_selection_port)
        await handler.execute_command(PopulateMachineIdsCommand(request_id=_VALID_REQUEST_ID))

        provider_selection_port.execute_operation.assert_not_called()

    @pytest.mark.asyncio
    async def test_machine_ids_saved_from_successful_result(self):
        """Discovered instance IDs must be persisted via update_machine_ids."""
        request = _make_request()

        async def _execute_operation(provider_name: str, operation: Operation) -> OperationResult:
            return OperationResult.success_result(
                data={
                    "instances": [
                        {"instance_id": "i-111"},
                        {"instance_id": "i-222"},
                    ]
                }
            )

        provider_selection_port = MagicMock()
        provider_selection_port.execute_operation = _execute_operation

        handler = _make_handler(request, provider_selection_port)
        await handler.execute_command(PopulateMachineIdsCommand(request_id=_VALID_REQUEST_ID))

        request.update_machine_ids.assert_called_once_with(["i-111", "i-222"])

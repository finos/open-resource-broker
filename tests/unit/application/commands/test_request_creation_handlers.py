"""Unit tests for request_creation_handlers — CreateMachineRequestHandler and CreateReturnRequestHandler."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.commands.request_creation_handlers import (
    CreateMachineRequestHandler,
    CreateReturnRequestHandler,
)
from orb.application.dto.commands import CreateRequestCommand, CreateReturnRequestCommand
from orb.domain.base.exceptions import ApplicationError, EntityNotFoundError
from orb.domain.base.ports import (
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
    ProviderSelectionPort,
)
from orb.domain.request.request_types import RequestStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEMPLATE_ID = "tmpl-abc"
_REQUEST_ID = "req-00000000-0000-0000-0000-000000000001"
_MACHINE_ID = "m-00000000-0000-0000-0000-000000000002"


def _make_template(template_id: str = _TEMPLATE_ID) -> MagicMock:
    t = MagicMock()
    t.template_id = template_id
    return t


def _make_request_aggregate(request_id: str = _REQUEST_ID) -> MagicMock:
    r = MagicMock()
    r.request_id = request_id
    r.metadata = {}
    r.update_status = MagicMock(return_value=r)
    return r


def _make_uow_factory(request_agg: MagicMock | None = None) -> MagicMock:
    uow = MagicMock()
    uow.requests.save = MagicMock(return_value=[])
    if request_agg is not None:
        uow.requests.get_by_id.return_value = request_agg
    uow.machines.get_by_id.return_value = None

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = _create
    return factory


def _make_machine_handler(
    *,
    template: MagicMock | None = None,
    selection_result: MagicMock | None = None,
    request_agg: MagicMock | None = None,
    provisioning_result: dict[str, Any] | None = None,
) -> tuple[CreateMachineRequestHandler, MagicMock]:
    """Build a CreateMachineRequestHandler bypassing __init__ to avoid lazy-import deps."""
    if template is None:
        template = _make_template()
    if request_agg is None:
        request_agg = _make_request_aggregate()
    if selection_result is None:
        selection_result = MagicMock()
    if provisioning_result is None:
        provisioning_result = {"success": True, "errors": []}

    uow_factory = _make_uow_factory(request_agg)
    query_bus = AsyncMock()
    query_bus.execute = AsyncMock(return_value=template)

    provisioning_service = AsyncMock()
    provisioning_service.execute_provisioning = AsyncMock(return_value=provisioning_result)

    provider_validation = AsyncMock()
    provider_validation.select_and_validate_provider = AsyncMock(return_value=selection_result)

    # Bypass __init__ to avoid lazy-imported service instantiation
    handler = object.__new__(CreateMachineRequestHandler)
    # Initialise BaseHandler / BaseCommandHandler state manually
    handler.logger = MagicMock(spec=LoggingPort)
    handler.error_handler = MagicMock(spec=ErrorHandlingPort)
    handler.event_publisher = MagicMock(spec=EventPublisherPort)
    handler._metrics = {}

    handler.uow_factory = uow_factory
    handler._container = MagicMock(spec=ContainerPort)
    handler._query_bus = query_bus
    handler._provider_selection_port = MagicMock(spec=ProviderSelectionPort)
    handler._provisioning_service = provisioning_service
    handler._provider_validation_service = provider_validation
    handler._request_creation_service = MagicMock()
    handler._request_creation_service.create_machine_request = MagicMock(return_value=request_agg)
    handler._status_service = AsyncMock()
    handler._status_service.update_request_from_provisioning = AsyncMock(return_value=request_agg)

    return handler, query_bus


def _make_return_handler(*, uow_factory: MagicMock | None = None) -> CreateReturnRequestHandler:
    """Build a CreateReturnRequestHandler bypassing __init__ to avoid lazy-import deps."""
    if uow_factory is None:
        uow_factory = _make_uow_factory()

    handler = object.__new__(CreateReturnRequestHandler)
    # Initialise BaseHandler / BaseCommandHandler state manually
    handler.logger = MagicMock(spec=LoggingPort)
    handler.error_handler = MagicMock(spec=ErrorHandlingPort)
    handler.event_publisher = MagicMock(spec=EventPublisherPort)
    handler._metrics = {}

    handler.uow_factory = uow_factory
    handler._container = MagicMock(spec=ContainerPort)
    handler._query_bus = AsyncMock()
    handler._provider_selection_port = MagicMock(spec=ProviderSelectionPort)
    handler._machine_grouping_service = MagicMock()
    handler._deprovisioning_orchestrator = AsyncMock()

    return handler


# ---------------------------------------------------------------------------
# CreateMachineRequestHandler — validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateMachineRequestHandlerValidation:
    @pytest.mark.asyncio
    async def test_validate_raises_when_template_id_missing(self):
        handler, _ = _make_machine_handler()
        cmd = CreateRequestCommand(template_id="", requested_count=1)
        with pytest.raises(ValueError, match="template_id is required"):
            await handler.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_validate_raises_when_requested_count_zero(self):
        handler, _ = _make_machine_handler()
        cmd = CreateRequestCommand(template_id="t1", requested_count=0)
        with pytest.raises(ValueError, match="requested_count must be positive"):
            await handler.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_validate_raises_when_requested_count_negative(self):
        handler, _ = _make_machine_handler()
        cmd = CreateRequestCommand(template_id="t1", requested_count=-5)
        with pytest.raises(ValueError, match="requested_count must be positive"):
            await handler.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_validate_passes_for_valid_command(self):
        handler, _ = _make_machine_handler()
        cmd = CreateRequestCommand(template_id="t1", requested_count=3)
        # Should not raise
        await handler.validate_command(cmd)


# ---------------------------------------------------------------------------
# CreateMachineRequestHandler — _load_template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateMachineRequestHandlerLoadTemplate:
    @pytest.mark.asyncio
    async def test_load_template_raises_when_query_bus_none(self):
        handler, _ = _make_machine_handler()
        handler._query_bus = None  # type: ignore[assignment]
        with pytest.raises(ApplicationError, match="QueryBus is required"):
            await handler._load_template("tmpl-1")

    @pytest.mark.asyncio
    async def test_load_template_raises_entity_not_found_when_template_missing(self):
        handler, query_bus = _make_machine_handler()
        query_bus.execute = AsyncMock(return_value=None)
        with pytest.raises(EntityNotFoundError):
            await handler._load_template("no-such-tmpl")

    @pytest.mark.asyncio
    async def test_load_template_returns_template(self):
        template = _make_template()
        handler, query_bus = _make_machine_handler(template=template)
        query_bus.execute = AsyncMock(return_value=template)
        result = await handler._load_template(_TEMPLATE_ID)
        assert result is template


# ---------------------------------------------------------------------------
# CreateMachineRequestHandler — dry-run path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateMachineRequestHandlerDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_skips_provisioning(self):
        request_agg = _make_request_aggregate()
        request_agg.metadata = {"dry_run": True}
        # update_status returns a new mock with the right status
        completed_agg = MagicMock()
        request_agg.update_status = MagicMock(return_value=completed_agg)

        handler, _ = _make_machine_handler(request_agg=request_agg)

        cmd = CreateRequestCommand(template_id=_TEMPLATE_ID, requested_count=2)
        await handler.execute_command(cmd)

        # Provisioning should NOT have been called
        handler._provisioning_service.execute_provisioning.assert_not_awaited()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_dry_run_calls_update_status_completed(self):
        request_agg = _make_request_aggregate()
        request_agg.metadata = {"dry_run": True}
        completed_agg = MagicMock()
        request_agg.update_status = MagicMock(return_value=completed_agg)

        handler, _ = _make_machine_handler(request_agg=request_agg)

        cmd = CreateRequestCommand(template_id=_TEMPLATE_ID, requested_count=1)
        await handler.execute_command(cmd)

        request_agg.update_status.assert_called_once_with(
            RequestStatus.COMPLETED, "Request created successfully (dry-run)"
        )


# ---------------------------------------------------------------------------
# CreateMachineRequestHandler — happy-path provisioning
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateMachineRequestHandlerProvisioning:
    @pytest.mark.asyncio
    async def test_execute_command_stores_request_id_in_command(self):
        request_agg = _make_request_aggregate(request_id=_REQUEST_ID)
        request_agg.metadata = {}
        handler, _ = _make_machine_handler(request_agg=request_agg)

        cmd = CreateRequestCommand(template_id=_TEMPLATE_ID, requested_count=1)
        await handler.execute_command(cmd)

        assert cmd.created_request_id == str(request_agg.request_id)

    @pytest.mark.asyncio
    async def test_execute_command_calls_provisioning_service(self):
        request_agg = _make_request_aggregate()
        request_agg.metadata = {}
        handler, _ = _make_machine_handler(request_agg=request_agg)

        cmd = CreateRequestCommand(template_id=_TEMPLATE_ID, requested_count=1)
        await handler.execute_command(cmd)

        handler._provisioning_service.execute_provisioning.assert_awaited_once()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_execute_command_calls_status_update_with_provisioning_result(self):
        request_agg = _make_request_aggregate()
        request_agg.metadata = {}
        prov_result = {"success": True, "errors": []}
        handler, _ = _make_machine_handler(request_agg=request_agg, provisioning_result=prov_result)

        cmd = CreateRequestCommand(template_id=_TEMPLATE_ID, requested_count=1)
        await handler.execute_command(cmd)

        # The provisioning result must be forwarded verbatim, alongside the
        # request aggregate produced by create_machine_request.
        handler._status_service.update_request_from_provisioning.assert_awaited_once_with(  # type: ignore[attr-defined]
            request_agg, prov_result
        )


# ---------------------------------------------------------------------------
# CreateMachineRequestHandler — _persist_and_publish event retry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPersistAndPublish:
    @pytest.mark.asyncio
    async def test_publish_failure_is_logged_not_raised(self):
        """Event publish errors must be swallowed after max retries."""
        request_agg = _make_request_aggregate()
        request_agg.metadata = {}

        uow = MagicMock()
        # Return one event from save
        event_obj = MagicMock()
        uow.requests.save = MagicMock(return_value=[event_obj])

        @contextmanager
        def _create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create

        # Build handler via object.__new__ (bypasses lazy imports in __init__)
        handler = object.__new__(CreateMachineRequestHandler)
        handler.logger = MagicMock(spec=LoggingPort)
        handler.error_handler = MagicMock(spec=ErrorHandlingPort)
        handler._metrics = {}
        handler.uow_factory = factory

        # Make event_publisher.publish always raise
        handler.event_publisher = MagicMock(spec=EventPublisherPort)
        handler.event_publisher.publish.side_effect = RuntimeError("publish failed")

        # Patch asyncio.sleep to avoid delays
        with patch(
            "orb.application.commands.request_creation_handlers.asyncio.sleep", new=AsyncMock()
        ):
            # Should not raise even though publish fails
            await handler._persist_and_publish(request_agg)

        handler.logger.error.assert_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_publish_success_on_first_attempt_no_error_logged(self):
        request_agg = _make_request_aggregate()
        request_agg.metadata = {}

        uow = MagicMock()
        event_obj = MagicMock()
        uow.requests.save = MagicMock(return_value=[event_obj])

        @contextmanager
        def _create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create

        handler = object.__new__(CreateMachineRequestHandler)
        handler.logger = MagicMock(spec=LoggingPort)
        handler.error_handler = MagicMock(spec=ErrorHandlingPort)
        handler._metrics = {}
        handler.uow_factory = factory
        handler.event_publisher = MagicMock(spec=EventPublisherPort)
        handler.event_publisher.publish = MagicMock()  # succeeds immediately

        await handler._persist_and_publish(request_agg)

        handler.logger.error.assert_not_called()


# ---------------------------------------------------------------------------
# CreateReturnRequestHandler — validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateReturnRequestHandlerValidation:
    @pytest.mark.asyncio
    async def test_validate_raises_when_machine_ids_empty(self):
        handler = _make_return_handler()
        cmd = CreateReturnRequestCommand(machine_ids=[])
        with pytest.raises(ValueError, match="machine_ids is required"):
            await handler.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_validate_single_machine_raises_entity_not_found_when_missing(self):
        uow = MagicMock()
        uow.machines.get_by_id.return_value = None

        @contextmanager
        def _create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create

        handler = _make_return_handler(uow_factory=factory)
        cmd = CreateReturnRequestCommand(machine_ids=[_MACHINE_ID])
        with pytest.raises(EntityNotFoundError):
            await handler.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_validate_single_machine_raises_when_already_has_return_request(self):
        uow = MagicMock()
        machine = MagicMock()
        machine.return_request_id = "existing-ret-req"
        uow.machines.get_by_id.return_value = machine

        @contextmanager
        def _create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create

        handler = _make_return_handler(uow_factory=factory)
        cmd = CreateReturnRequestCommand(machine_ids=[_MACHINE_ID], force_return=False)

        from orb.domain.request.exceptions import RequestValidationError

        with pytest.raises(RequestValidationError):
            await handler.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_validate_single_machine_force_return_does_not_raise(self):
        uow = MagicMock()
        machine = MagicMock()
        machine.return_request_id = "existing-ret-req"
        uow.machines.get_by_id.return_value = machine

        @contextmanager
        def _create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create

        handler = _make_return_handler(uow_factory=factory)
        cmd = CreateReturnRequestCommand(machine_ids=[_MACHINE_ID], force_return=True)
        # Should not raise
        await handler.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_validate_single_machine_no_return_request_passes(self):
        uow = MagicMock()
        machine = MagicMock()
        machine.return_request_id = None
        uow.machines.get_by_id.return_value = machine

        @contextmanager
        def _create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create

        handler = _make_return_handler(uow_factory=factory)
        cmd = CreateReturnRequestCommand(machine_ids=[_MACHINE_ID])
        # Should not raise
        await handler.validate_command(cmd)


# ---------------------------------------------------------------------------
# CreateReturnRequestHandler — _filter_machines
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFilterMachines:
    def _make_handler_with_machines(
        self, machines: dict[str, MagicMock]
    ) -> CreateReturnRequestHandler:
        uow = MagicMock()
        uow.machines.get_by_id.side_effect = lambda mid: machines.get(mid)

        @contextmanager
        def _create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create
        return _make_return_handler(uow_factory=factory)

    def test_missing_machine_is_skipped(self):
        handler = self._make_handler_with_machines({})
        valid, skipped = handler._filter_machines(["missing-id"])
        assert valid == []
        assert skipped[0]["machine_id"] == "missing-id"
        assert "not found" in skipped[0]["reason"].lower()

    def test_machine_with_existing_return_request_skipped_when_no_force(self):
        machine = MagicMock()
        machine.return_request_id = "old-req"
        handler = self._make_handler_with_machines({_MACHINE_ID: machine})
        valid, skipped = handler._filter_machines([_MACHINE_ID], force_return=False)
        assert valid == []
        assert skipped[0]["machine_id"] == _MACHINE_ID

    def test_machine_with_existing_return_request_included_with_force(self):
        machine = MagicMock()
        machine.return_request_id = "old-req"
        handler = self._make_handler_with_machines({_MACHINE_ID: machine})
        valid, skipped = handler._filter_machines([_MACHINE_ID], force_return=True)
        assert _MACHINE_ID in valid
        assert skipped == []

    def test_clean_machine_always_included(self):
        machine = MagicMock()
        machine.return_request_id = None
        handler = self._make_handler_with_machines({_MACHINE_ID: machine})
        valid, skipped = handler._filter_machines([_MACHINE_ID])
        assert valid == [_MACHINE_ID]
        assert skipped == []


# ---------------------------------------------------------------------------
# CreateReturnRequestHandler — execute_command: all-invalid machines
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateReturnRequestHandlerExecute:
    @pytest.mark.asyncio
    async def test_execute_with_all_invalid_machines_sets_empty_result(self):
        """When no valid machines remain, command result fields are empty."""
        uow = MagicMock()
        uow.machines.get_by_id.return_value = None  # every machine is "not found"

        @contextmanager
        def _create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create

        handler = _make_return_handler(uow_factory=factory)
        # Inject stub machine grouping service
        handler._machine_grouping_service = MagicMock()

        cmd = CreateReturnRequestCommand(machine_ids=["m1", "m2"])
        await handler.execute_command(cmd)

        assert cmd.created_request_ids == []
        assert cmd.processed_machines == []

    @pytest.mark.asyncio
    async def test_execute_single_machine_delegates_to_deprovisioning(self):
        """Single-machine path uses valid_machines directly without filter."""
        uow = MagicMock()
        machine = MagicMock()
        machine.return_request_id = None
        uow.machines.get_by_id.return_value = machine
        uow.requests.save = MagicMock(return_value=[])
        uow.machines.save = MagicMock()

        @contextmanager
        def _create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create

        handler = _make_return_handler(uow_factory=factory)

        # Stub services
        group_key = ("aws", "aws-provider", "EC2")
        handler._machine_grouping_service = MagicMock()
        handler._machine_grouping_service.group_by_provider.return_value = {
            group_key: [_MACHINE_ID]
        }
        handler._machine_grouping_service.group_by_resource.return_value = (
            [MagicMock()],
            [],
        )
        handler._deprovisioning_orchestrator = AsyncMock()
        handler._deprovisioning_orchestrator.execute_deprovisioning = AsyncMock(
            return_value={"success": True}
        )

        # stub the container / command_bus calls
        command_bus = AsyncMock()
        command_bus.execute = AsyncMock()
        handler._container = MagicMock()
        handler._container.get.return_value = command_bus

        cmd = CreateReturnRequestCommand(machine_ids=[_MACHINE_ID])

        with (
            patch(
                "orb.application.commands.request_creation_handlers.Request.create_return_request"
            ) as mock_create,
            patch("orb.application.commands.request_creation_handlers.RequestId"),
        ):
            return_req = MagicMock()
            return_req.request_id = "ret-req-1"
            mock_create.return_value = return_req

            await handler.execute_command(cmd)

        assert cmd.created_request_ids is not None
        assert len(cmd.created_request_ids) == 1

    @pytest.mark.asyncio
    async def test_execute_propagates_exception_after_logging(self):
        """Top-level exception is re-raised after being logged."""
        uow = MagicMock()
        machine = MagicMock()
        machine.return_request_id = None
        uow.machines.get_by_id.return_value = machine
        uow.requests.save = MagicMock(return_value=[])

        @contextmanager
        def _create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create

        handler = _make_return_handler(uow_factory=factory)
        handler._machine_grouping_service = MagicMock()
        handler._machine_grouping_service.group_by_provider.side_effect = RuntimeError("boom")

        cmd = CreateReturnRequestCommand(machine_ids=[_MACHINE_ID, "m2"])

        with pytest.raises(RuntimeError, match="boom"):
            await handler.execute_command(cmd)

        handler.logger.error.assert_called()  # type: ignore[attr-defined]

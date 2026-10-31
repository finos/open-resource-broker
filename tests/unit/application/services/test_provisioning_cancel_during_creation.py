"""A request cancelled while its resources are being created must not leak them.

The cancel finds nothing to abort (no resource ids are recorded yet) and flips the
request. Provisioning then re-checks the stored request after its guarded
checkpoint save: it must never move a cancelled request back to an in-progress
state, and it must abort what it just created.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
    ProvisioningResult,
)
from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.base.operations import OperationType
from orb.domain.base.results import ProviderSelectionResult
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType

pytestmark = pytest.mark.unit


def _selection() -> ProviderSelectionResult:
    return ProviderSelectionResult(
        provider_name="aws-1", provider_type="aws", selection_reason="t", confidence=1.0
    )


def _request(provider_api: str = "EC2Fleet") -> Request:
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl",
        machine_count=2,
        provider_type="aws",
        provider_name="aws-1",
    )
    return request.model_copy(update={"provider_api": provider_api})


def _template(provider_api: str = "EC2Fleet") -> Any:
    return SimpleNamespace(template_id="tpl", provider_api=provider_api)


class _Store:
    """Stand-in request store whose stored status the test controls."""

    def __init__(self, stored_status: Optional[RequestStatus]) -> None:
        self.stored_status = stored_status
        self.saved: list[Request] = []
        self.save_error: Optional[Exception] = None
        self.reads = 0
        self.stored_resource_ids: list[str] = []

    def stored_request(self) -> Request:
        stored = _request().model_copy(update={"status": self.stored_status})
        return stored.model_copy(update={"resource_ids": list(self.stored_resource_ids)})

    def factory(self) -> MagicMock:
        store = self

        uow = MagicMock()

        def find_by_id(request_id: str) -> Any:
            store.reads += 1
            if store.stored_status is None:
                return None
            return store.stored_request()

        def save(request: Request) -> list[Any]:
            if store.save_error is not None:
                raise store.save_error
            store.saved.append(request)
            store.stored_resource_ids = list(request.resource_ids)
            return []

        uow.requests.find_by_id.side_effect = find_by_id
        uow.requests.get_by_id.side_effect = find_by_id
        uow.requests.save.side_effect = save

        @contextmanager
        def create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = create
        return factory


def _service(
    store: _Store, supports: bool = True, abort_result: Any = None
) -> tuple[ProvisioningOrchestrationService, MagicMock]:
    port = MagicMock()
    capabilities = MagicMock()
    capabilities.supports_operation.side_effect = lambda op: (
        supports and op == OperationType.CANCEL_PROVISIONING
    )
    port.get_strategy_capabilities.return_value = capabilities
    port.execute_operation = AsyncMock(
        return_value=abort_result or SimpleNamespace(success=True, error_message=None)
    )
    config_port = MagicMock()
    config_port.get_request_config.return_value = {
        "fulfillment_max_retries": 2,
        "fulfillment_timeout_seconds": 300,
        "dispatch_timeout_seconds": 10.0,
        "fulfillment_batch_size": 1000,
    }
    circuit_breaker_factory = MagicMock()
    circuit_breaker_factory.return_value = MagicMock(has_state=MagicMock(return_value=False))
    service = ProvisioningOrchestrationService(
        logger=MagicMock(),
        uow_factory=store.factory(),
        scheduler=MagicMock(),
        provider_selection_port=port,
        provider_config_port=MagicMock(),
        config_port=config_port,
        circuit_breaker_factory=circuit_breaker_factory,
    )
    return service, port


def _result(resource_ids: list[str], final: bool = False) -> ProvisioningResult:
    return ProvisioningResult(
        success=True,
        resource_ids=resource_ids,
        machine_ids=[],
        instances=[],
        provider_data={},
        fulfilled_count=0,
        is_final=final,
    )


class TestGuardedCheckpoint:
    def test_a_cancelled_request_is_never_overwritten(self):
        store = _Store(RequestStatus.CANCELLED)
        service, _ = _service(store)

        updated, ok = service._persist_resource_ids_checkpoint(_request(), ["fleet-1"])

        # The cancel stands: the checkpoint keeps the ids but never moves the status.
        assert ok is True
        assert updated.status == RequestStatus.CANCELLED
        assert [r.status for r in store.saved] == [RequestStatus.CANCELLED]

    def test_an_active_request_is_checkpointed_as_before(self):
        store = _Store(RequestStatus.PENDING)
        service, _ = _service(store)

        updated, ok = service._persist_resource_ids_checkpoint(_request(), ["fleet-1"])

        assert ok is True
        assert len(store.saved) == 1
        assert store.saved[0].status == RequestStatus.ACQUIRING
        assert "fleet-1" in store.saved[0].resource_ids


class TestAbortWhatWasJustCreated:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider_api", ["EC2Fleet", "SpotFleet", "ASG", "RunInstances"])
    async def test_resources_created_for_a_cancelled_request_are_aborted_for_each_api(
        self, provider_api
    ):
        store = _Store(RequestStatus.CANCELLED)
        service, port = _service(store)
        dispatches = AsyncMock(side_effect=[_result(["res-1"]), _result(["res-2"], final=True)])

        with patch.object(service, "_dispatch_single_attempt", dispatches):
            result = await service.execute_provisioning(
                _template(provider_api), _request(provider_api), _selection()
            )

        assert result.cancelled is True
        assert dispatches.await_count == 1  # the loop stops: no second fleet is created
        # Nothing but the resource ids is written, and the request stays cancelled.
        assert all(r.status == RequestStatus.CANCELLED for r in store.saved)
        provider_id, operation = port.execute_operation.await_args.args
        assert provider_id == "aws-1"
        assert operation.operation_type == OperationType.CANCEL_PROVISIONING
        assert operation.parameters["provider_api"] == provider_api
        assert operation.parameters["resource_ids"] == ["res-1"]

    @pytest.mark.asyncio
    async def test_a_version_conflict_on_the_checkpoint_is_resolved_by_re_reading(self):
        store = _Store(RequestStatus.PENDING)
        service, port = _service(store)
        store.save_error = ConcurrencyError("version conflict")

        async def dispatch(*args: Any, **kwargs: Any) -> ProvisioningResult:
            # The cancel lands between the create and the checkpoint.
            store.stored_status = RequestStatus.CANCELLED
            return _result(["fleet-1"])

        with patch.object(service, "_dispatch_single_attempt", side_effect=dispatch):
            result = await service.execute_provisioning(_template(), _request(), _selection())

        assert result.cancelled is True
        assert port.execute_operation.await_args.args[1].parameters["resource_ids"] == ["fleet-1"]

    @pytest.mark.asyncio
    async def test_a_request_that_is_not_cancelled_is_left_alone(self):
        store = _Store(RequestStatus.PENDING)
        service, port = _service(store)
        dispatches = AsyncMock(side_effect=[_result(["res-1"], final=True)])

        with patch.object(service, "_dispatch_single_attempt", dispatches):
            result = await service.execute_provisioning(_template(), _request(), _selection())

        assert result.cancelled is False
        port.execute_operation.assert_not_awaited()
        assert len(store.saved) == 1

    @pytest.mark.asyncio
    async def test_every_resource_created_so_far_is_aborted_not_only_the_latest(self):
        store = _Store(RequestStatus.PENDING)
        service, port = _service(store)
        calls = {"n": 0}

        async def dispatch(*args: Any, **kwargs: Any) -> ProvisioningResult:
            calls["n"] += 1
            if calls["n"] == 2:
                store.stored_status = RequestStatus.CANCELLED
            return _result([f"res-{calls['n']}"])

        with patch.object(service, "_dispatch_single_attempt", side_effect=dispatch):
            result = await service.execute_provisioning(_template(), _request(), _selection())

        assert result.cancelled is True
        assert port.execute_operation.await_args.args[1].parameters["resource_ids"] == [
            "res-1",
            "res-2",
        ]


class TestUnabortableResourcesAreAlerted:
    @staticmethod
    def _events(service: ProvisioningOrchestrationService) -> list[str]:
        return [
            c.kwargs["extra"]["event"]
            for c in service._logger.error.call_args_list
            if "extra" in c.kwargs and "event" in c.kwargs["extra"]
        ]

    @pytest.mark.asyncio
    async def test_a_failed_abort_raises_an_alertable_record_naming_the_resources(self):
        store = _Store(RequestStatus.CANCELLED)
        service, _ = _service(
            store, abort_result=SimpleNamespace(success=False, error_message="denied")
        )

        aborted = await service.abort_if_cancelled(
            _request(), _selection(), ["fleet-1", "fleet-2"], "EC2Fleet"
        )

        assert aborted is True
        assert self._events(service) == ["request.created_after_cancel"]
        message = service._logger.error.call_args.args
        assert "fleet-1" in str(message) and "fleet-2" in str(message)

    @pytest.mark.asyncio
    async def test_a_provider_that_cannot_abort_is_alerted(self):
        store = _Store(RequestStatus.CANCELLED)
        service, port = _service(store, supports=False)

        assert await service.abort_if_cancelled(_request(), _selection(), ["f-1"], "EC2Fleet")

        port.execute_operation.assert_not_awaited()
        assert self._events(service) == ["request.created_after_cancel"]

    @pytest.mark.asyncio
    async def test_a_provider_exception_is_alerted_not_raised(self):
        store = _Store(RequestStatus.CANCELLED)
        service, port = _service(store)
        port.execute_operation.side_effect = RuntimeError("provider down")

        assert await service.abort_if_cancelled(_request(), _selection(), ["f-1"], "EC2Fleet")

        assert self._events(service) == ["request.created_after_cancel"]

    @pytest.mark.asyncio
    async def test_nothing_is_created_means_nothing_is_checked(self):
        store = _Store(RequestStatus.CANCELLED)
        service, port = _service(store)

        assert await service.abort_if_cancelled(_request(), _selection(), [], "EC2Fleet") is False

        assert store.reads == 0
        port.execute_operation.assert_not_awaited()


class TestCancelledRequestKeepsTheResourceIds:
    @pytest.mark.asyncio
    async def test_a_throttled_abort_leaves_the_ids_on_the_cancelled_request(self):
        """The ids stay recorded, status unchanged, so a later cancel can abort again."""
        store = _Store(RequestStatus.CANCELLED)
        service, port = _service(store)
        port.execute_operation.side_effect = RuntimeError("RequestLimitExceeded")

        assert await service.abort_if_cancelled(
            _request(), _selection(), ["f-1", "f-2"], "EC2Fleet"
        )

        assert len(store.saved) == 1
        assert store.saved[0].status == RequestStatus.CANCELLED
        assert store.saved[0].resource_ids == ["f-1", "f-2"]

    @pytest.mark.asyncio
    async def test_a_successful_abort_records_the_ids_too(self):
        store = _Store(RequestStatus.CANCELLED)
        service, _ = _service(store)

        await service.abort_if_cancelled(_request(), _selection(), ["f-1"], "EC2Fleet")

        assert store.saved[0].resource_ids == ["f-1"]

    @pytest.mark.asyncio
    async def test_the_launched_instances_are_recorded_on_the_cancelled_request(self):
        store = _Store(RequestStatus.CANCELLED)
        service, _ = _service(store)

        await service.abort_if_cancelled(
            _request("RunInstances"), _selection(), ["r-1"], "RunInstances", ["i-1", "i-2"]
        )

        assert sorted(store.saved[0].machine_ids) == ["i-1", "i-2"]

    @pytest.mark.asyncio
    async def test_ids_are_not_added_to_a_request_that_is_not_cancelled(self):
        store = _Store(RequestStatus.PENDING)
        service, _ = _service(store)

        service._record_ids_on_cancelled_request(_request(), ["f-1"])

        assert store.saved == []

"""ProviderRegistryService.execute_operation gates on readiness and capability."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.provider_registry_service import ProviderRegistryService
from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
)
from orb.application.services.request_status_management_service import (
    RequestStatusManagementService,
)
from orb.domain.base.exceptions import (
    DomainException,
    InfrastructureError,
    OperationNotSupportedError,
    ProviderNotReadyError,
    QuotaError,
)
from orb.domain.base.operation_outcome import Failed
from orb.domain.base.operations import Operation, OperationType
from orb.domain.base.results import ProviderSelectionResult
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine
from orb.domain.request.request_identifiers import RequestId
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType
from orb.infrastructure.resilience import retry_classifier_registry
from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy
from orb.infrastructure.resilience.strategy.exponential import ExponentialBackoffStrategy
from orb.providers.base.strategy.provider_strategy import ProviderCapabilities, ProviderResult


def _strategy(*, initialized: bool = True, supported: tuple[OperationType, ...] = ()) -> MagicMock:
    strategy = MagicMock()
    strategy.is_initialized = initialized
    strategy.get_capabilities.return_value = ProviderCapabilities(
        provider_type="fake",
        supported_operations=list(supported) or [OperationType.CREATE_INSTANCES],
    )
    strategy.execute_operation = AsyncMock(return_value=ProviderResult.success_result({"ok": 1}))
    return strategy


def _service(strategy: Any) -> ProviderRegistryService:
    registry = MagicMock()
    registry.get_or_create_strategy.return_value = strategy
    return ProviderRegistryService(registry, MagicMock(), MagicMock())


def _op(op_type: OperationType = OperationType.CREATE_INSTANCES) -> Operation:
    return Operation(operation_type=op_type, parameters={})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_supported_operation_on_ready_strategy_is_executed() -> None:
    strategy = _strategy()
    result = await _service(strategy).execute_operation("p1", _op())
    assert result.success is True
    strategy.execute_operation.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_uninitialized_strategy_raises_not_ready_before_dispatch() -> None:
    strategy = _strategy(initialized=False)
    with pytest.raises(ProviderNotReadyError) as info:
        await _service(strategy).execute_operation("p1", _op())
    assert info.value.provider_id == "p1"
    assert info.value.operation_type == OperationType.CREATE_INSTANCES
    strategy.execute_operation.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unsupported_operation_raises_before_dispatch() -> None:
    strategy = _strategy(supported=(OperationType.CREATE_INSTANCES,))
    with pytest.raises(OperationNotSupportedError) as info:
        await _service(strategy).execute_operation("p1", _op(OperationType.TAG_INSTANCES))
    assert info.value.operation_type == OperationType.TAG_INSTANCES
    strategy.execute_operation.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cached_strategy_that_loses_readiness_is_rejected_on_the_next_call() -> None:
    strategy = _strategy()
    service = _service(strategy)
    await service.execute_operation("p1", _op())

    strategy.is_initialized = False
    with pytest.raises(ProviderNotReadyError):
        await service.execute_operation("p1", _op())
    assert strategy.execute_operation.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_strategy_still_raises_value_error() -> None:
    with pytest.raises(ValueError, match="No strategy found"):
        await _service(None).execute_operation("p1", _op())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_strategy_exceptions_still_propagate() -> None:
    strategy = _strategy()
    strategy.execute_operation = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await _service(strategy).execute_operation("p1", _op())


# ---------------------------------------------------------------------------
# Retry and circuit classification
# ---------------------------------------------------------------------------

_GATE_ERRORS = [
    ProviderNotReadyError("p1", OperationType.CREATE_INSTANCES),
    OperationNotSupportedError("p1", OperationType.CREATE_INSTANCES),
]


@pytest.mark.unit
@pytest.mark.parametrize("error", _GATE_ERRORS, ids=lambda e: type(e).__name__)
def test_gate_errors_derive_directly_from_domain_exception(error: DomainException) -> None:
    assert type(error).__bases__ == (DomainException,)
    assert not isinstance(error, (InfrastructureError, QuotaError))


@pytest.mark.unit
@pytest.mark.parametrize("error", _GATE_ERRORS, ids=lambda e: type(e).__name__)
def test_exponential_backoff_does_not_retry_gate_errors(error: Exception) -> None:
    assert ExponentialBackoffStrategy().should_retry(0, error) is False
    assert ExponentialBackoffStrategy().should_retry(0, RuntimeError("transient")) is True


@pytest.mark.unit
@pytest.mark.parametrize("error", _GATE_ERRORS, ids=lambda e: type(e).__name__)
def test_circuit_breaker_neither_retries_nor_counts_gate_errors(error: Exception) -> None:
    breaker = CircuitBreakerStrategy(service_name="gate-test")
    before = breaker._circuit_states["gate-test"]["failure_count"]
    assert breaker.should_retry(0, error) is False
    assert breaker._circuit_states["gate-test"]["failure_count"] == before


@pytest.mark.unit
def test_core_classifier_survives_clearing_provider_classifiers() -> None:
    retry_classifier_registry.clear_classifiers()
    assert retry_classifier_registry.is_non_retryable(_GATE_ERRORS[0]) is True
    assert retry_classifier_registry.is_non_retryable(_GATE_ERRORS[1]) is True
    assert retry_classifier_registry.is_non_retryable(RuntimeError("x")) is False


# ---------------------------------------------------------------------------
# Provisioning dispatch (CREATE_INSTANCES) with the gate's errors
# ---------------------------------------------------------------------------


def _provisioning_service(error: Exception) -> tuple[ProvisioningOrchestrationService, MagicMock]:
    dispatch = AsyncMock(side_effect=error)
    selection_port = MagicMock()
    selection_port.execute_operation = dispatch
    config_port = MagicMock()
    config_port.get_request_config.return_value = {}
    breaker = MagicMock()
    breaker.has_state.return_value = True
    scheduler = MagicMock()
    scheduler.format_template_for_provider.return_value = {}
    service = ProvisioningOrchestrationService(
        logger=MagicMock(),
        uow_factory=MagicMock(),
        scheduler=scheduler,
        provider_selection_port=selection_port,
        provider_config_port=MagicMock(),
        config_port=config_port,
        circuit_breaker_factory=MagicMock(return_value=breaker),
    )
    return service, breaker


def _request() -> Request:
    return Request(
        request_id=RequestId(value="req-00000000-0000-0000-0000-000000000009"),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        template_id="tmpl-1",
        requested_count=2,
        status=RequestStatus.PENDING,
    )


def _selection() -> ProviderSelectionResult:
    return ProviderSelectionResult(
        provider_name="p1", provider_type="aws", selection_reason="t", confidence=1.0
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "recoverable"),
    [
        (ProviderNotReadyError("p1", OperationType.CREATE_INSTANCES), True),
        (OperationNotSupportedError("p1", OperationType.CREATE_INSTANCES), False),
    ],
    ids=["not_ready", "not_supported"],
)
async def test_provisioning_fails_once_without_retry_or_circuit_failure(
    error: Exception, recoverable: bool
) -> None:
    attempt, _ = _provisioning_service(error)
    dispatched = await attempt._dispatch_single_attempt(
        MagicMock(template_id="tmpl-1"), _request(), _selection(), count=2
    )
    assert isinstance(dispatched.outcome, Failed)
    assert dispatched.outcome.recoverable is recoverable
    assert dispatched.counts_as_provider_failure is False

    service, breaker = _provisioning_service(error)
    template = MagicMock(template_id="tmpl-1")

    result = await service.execute_provisioning(template, _request(), _selection())

    assert result.success is False
    assert result.machine_ids == []
    assert result.resource_ids == []
    assert result.error_message
    # Dispatched exactly once, and the provider's circuit breaker is untouched.
    assert service._provider_selection_port.execute_operation.await_count == 1  # type: ignore[attr-defined]
    breaker.record_failure.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_other_dispatch_failures_still_count_against_the_circuit() -> None:
    service, breaker = _provisioning_service(RuntimeError("provider exploded"))
    result = await service.execute_provisioning(
        MagicMock(template_id="tmpl-1"), _request(), _selection()
    )
    assert result.success is False
    breaker.record_failure.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ProviderNotReadyError("p1", OperationType.CREATE_INSTANCES),
        OperationNotSupportedError("p1", OperationType.CREATE_INSTANCES),
    ],
    ids=["not_ready", "not_supported"],
)
async def test_request_ends_failed_not_stuck_in_flight(error: Exception) -> None:
    service, _ = _provisioning_service(error)
    request = _request()
    result = await service.execute_provisioning(
        MagicMock(template_id="tmpl-1"), request, _selection()
    )

    status_service = RequestStatusManagementService(
        MagicMock(), MagicMock(), state_machine=FulfilmentStateMachine(grace_period_seconds=3600)
    )
    updated = await status_service.update_request_from_provisioning(request, result)

    assert updated.status == RequestStatus.FAILED

"""A pod release that deleted only some pods is reported as a partial failure."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.deprovisioning_orchestrator import DeprovisioningOrchestrator
from orb.domain.base.operation_outcome import Accepted, Failed
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy


def _strategy(release_result: dict) -> K8sProviderStrategy:
    fake_client = MagicMock()
    fake_client.core_v1 = MagicMock()
    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(), logger=MagicMock(), kubernetes_client=fake_client
    )
    strategy.initialize()
    handler = MagicMock()
    handler.release_hosts = AsyncMock(return_value=release_result)
    strategy._handler_registry.get_handler = MagicMock(return_value=handler)  # type: ignore[method-assign]
    strategy._handler_registry.resolve_provider_api = MagicMock(return_value="Pod")  # type: ignore[method-assign]
    return strategy


_REQUEST = SimpleNamespace(request_id="req-1", provider_data={"namespace": "default"})
_PARTIAL = {"deleted": ["pod-a"], "failed_deletes": [("pod-b", "forbidden")]}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_delete_returns_recoverable_failed_with_nested_ids() -> None:
    strategy = _strategy(_PARTIAL)

    outcome = await strategy.return_machines(["pod-a", "pod-b"], _REQUEST)

    assert isinstance(outcome, Failed)
    assert outcome.recoverable is True
    assert "pod-b" not in outcome.error
    assert outcome.metadata["provider_data"] == {
        "successful_instance_ids": ["pod-a"],
        "failed_instance_ids": ["pod-b"],
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_full_delete_still_accepted() -> None:
    strategy = _strategy({"deleted": ["pod-a", "pod-b"], "failed_deletes": []})

    outcome = await strategy.return_machines(["pod-a", "pod-b"], _REQUEST)

    assert isinstance(outcome, Accepted)
    assert outcome.pending_resource_ids == ["pod-a", "pod-b"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bridge_exposes_ids_where_the_orchestrator_reads_them() -> None:
    strategy = _strategy(_PARTIAL)
    operation = ProviderOperation(
        operation_type=ProviderOperationType.TERMINATE_INSTANCES,
        parameters={"instance_ids": ["pod-a", "pod-b"], "request": _REQUEST},
    )

    result = await strategy._handle_terminate_instances(operation)

    assert result.success is False
    assert result.metadata["provider_data"]["successful_instance_ids"] == ["pod-a"]
    assert result.metadata["provider_data"]["failed_instance_ids"] == ["pod-b"]

    selection = MagicMock()
    selection.execute_operation = AsyncMock(return_value=result)
    query_bus = MagicMock()
    query_bus.execute = AsyncMock(return_value=MagicMock())
    orchestrator = DeprovisioningOrchestrator(
        uow_factory=MagicMock(),
        logger=MagicMock(),
        container=MagicMock(),
        query_bus=query_bus,
        provider_selection_port=selection,
    )
    machines = []
    for name in ("pod-a", "pod-b"):
        machine = MagicMock()
        machine.machine_id.value = name
        machine.template_id = "tmpl"
        machine.request_id = "req"
        machines.append(machine)

    overall = await orchestrator.execute_deprovisioning(
        {("k8s", "Pod", "res"): machines}, MagicMock(request_id="req-ret")
    )

    assert overall["partial"] is True
    assert overall["successful_instance_ids"] == ["pod-a"]
    assert overall["failed_instance_ids"] == ["pod-b"]

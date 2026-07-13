"""Regression test: k8s return must delete the acquire-time controller.

A return request carries its own request_id and no controller name in
provider_data.  Without threading the deprovisioning operation's resource_id
(the acquire-time controller name) and origin request_id into release, the
controller-backed handlers (Deployment/StatefulSet/Job) resolve the WRONG name,
the delete 404-no-ops, and the Deployment leaks.  This test pins that the
override reaches release_hosts.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy


def _strategy_with_mock_handler() -> tuple[K8sProviderStrategy, MagicMock]:
    fake_client = MagicMock()
    fake_client.core_v1 = MagicMock()
    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        kubernetes_client=fake_client,
    )
    strategy.initialize()

    handler = MagicMock()
    handler.release_hosts = AsyncMock(return_value={"deleted": ["m-0"]})
    # Force the registry to resolve our mock handler for any provider_api.
    strategy._handler_registry.get_handler = MagicMock(return_value=handler)  # type: ignore[method-assign]
    strategy._handler_registry.resolve_provider_api = MagicMock(return_value="Deployment")  # type: ignore[method-assign]
    return strategy, handler


@pytest.mark.asyncio
async def test_terminate_threads_acquire_name_into_release() -> None:
    strategy, handler = _strategy_with_mock_handler()

    # The RETURN request: its own id, and NO controller name in provider_data.
    return_request = SimpleNamespace(
        request_id="req-RETURN-9999",
        provider_data={"namespace": "default"},
    )

    op = ProviderOperation(
        operation_type=ProviderOperationType.TERMINATE_INSTANCES,
        parameters={
            "machine_ids": ["m-0"],
            "request": return_request,
            # deprovisioning supplies the acquire-time controller name + origin id
            "resource_id": "orb-ACQUIRE-abc123",
            "request_id": "req-ACQUIRE-abc123",
        },
    )

    result = await strategy._handle_terminate_instances(op)
    assert result.success

    handler.release_hosts.assert_awaited_once()
    _, provider_data = handler.release_hosts.await_args[0]
    # The acquire-time controller name must reach release, NOT the return id.
    assert provider_data["deployment_name"] == "orb-ACQUIRE-abc123"
    assert provider_data["request_id"] == "req-ACQUIRE-abc123"
    assert provider_data["request_id"] != "req-RETURN-9999"


@pytest.mark.asyncio
async def test_terminate_without_resource_id_leaves_provider_data_from_request() -> None:
    """No override supplied → falls back to the request's own provider_data."""
    strategy, handler = _strategy_with_mock_handler()
    return_request = SimpleNamespace(
        request_id="req-XYZ",
        provider_data={"namespace": "default", "deployment_name": "orb-XYZ"},
    )
    op = ProviderOperation(
        operation_type=ProviderOperationType.TERMINATE_INSTANCES,
        parameters={"machine_ids": ["m-0"], "request": return_request},
    )
    result = await strategy._handle_terminate_instances(op)
    assert result.success
    _, provider_data = handler.release_hosts.await_args[0]
    assert provider_data["deployment_name"] == "orb-XYZ"

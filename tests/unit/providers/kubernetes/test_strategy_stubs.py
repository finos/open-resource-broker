"""Unit tests for :class:`KubernetesProviderStrategy` Phase A surface.

Covers:

* config validation in the constructor;
* health check happy-path + failure-path using a mocked ``KubernetesClient``;
* capability advertisement;
* identity + naming helpers;
* the ``NotImplementedError`` stubs for the typed provisioning interface
  that Phase B fills in.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.base.strategy import ProviderOperationType
from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.strategy.kubernetes_provider_strategy import (
    KubernetesProviderStrategy,
)


def _make_strategy(
    *,
    api_resources: object | None = None,
    api_failure: Exception | None = None,
) -> KubernetesProviderStrategy:
    """Build a strategy with a mocked :class:`KubernetesClient`."""
    fake_core_v1 = MagicMock()
    if api_failure is not None:
        fake_core_v1.get_api_resources.side_effect = api_failure
    else:
        fake_core_v1.get_api_resources.return_value = (
            api_resources
            if api_resources is not None
            else SimpleNamespace(group_version="v1", resources=[object(), object(), object()])
        )

    fake_client = MagicMock()
    fake_client.core_v1 = fake_core_v1

    strategy = KubernetesProviderStrategy(
        config=KubernetesProviderConfig(),
        logger=MagicMock(),
        kubernetes_client=fake_client,
    )
    assert strategy.initialize() is True
    return strategy


def test_constructor_rejects_wrong_config_type() -> None:
    with pytest.raises(ValueError, match="requires KubernetesProviderConfig"):
        KubernetesProviderStrategy(
            config="not a config",  # type: ignore[arg-type]
            logger=MagicMock(),
        )


def test_provider_type_is_kubernetes() -> None:
    strategy = _make_strategy()
    assert strategy.provider_type == "kubernetes"


def test_get_capabilities_lists_v1_apis() -> None:
    caps = _make_strategy().get_capabilities()
    assert caps.provider_type == "kubernetes"
    assert caps.supports_operation(ProviderOperationType.CREATE_INSTANCES) is True
    assert caps.supports_operation(ProviderOperationType.TERMINATE_INSTANCES) is True
    assert caps.supports_operation(ProviderOperationType.GET_INSTANCE_STATUS) is True
    assert set(caps.supported_apis) == {
        "KubernetesPod",
        "KubernetesDeployment",
        "KubernetesStatefulSet",
        "KubernetesJob",
    }


def test_check_health_happy_path() -> None:
    status = _make_strategy().check_health()
    assert status.is_healthy is True
    assert "Kubernetes API server reachable" in status.status_message
    assert status.response_time_ms is not None


def test_check_health_unhappy_path() -> None:
    status = _make_strategy(api_failure=RuntimeError("boom")).check_health()
    assert status.is_healthy is False
    assert "boom" in status.status_message
    assert status.error_details is not None
    assert status.error_details["error"] == "boom"


def test_get_available_regions_is_empty() -> None:
    """Kubernetes has contexts, not regions."""
    assert _make_strategy().get_available_regions() == []
    assert _make_strategy().get_default_region() == ""


def test_naming_helpers_round_trip_context() -> None:
    strategy = _make_strategy()
    name = strategy.generate_provider_name({"context": "prod"})
    assert name == "kubernetes_prod"
    parsed = strategy.parse_provider_name(name)
    assert parsed["context_or_namespace"] == "prod"


def test_naming_helpers_fall_back_to_namespace() -> None:
    strategy = _make_strategy()
    name = strategy.generate_provider_name({"namespace": "orb-system"})
    assert name == "kubernetes_orb-system"


def test_provider_name_pattern() -> None:
    assert _make_strategy().get_provider_name_pattern() == "kubernetes_{context_or_namespace}"


@pytest.mark.asyncio
async def test_execute_operation_health_check_dispatches() -> None:
    """The Phase A dispatcher handles HEALTH_CHECK end-to-end."""
    from orb.providers.base.strategy import ProviderOperation

    strategy = _make_strategy()
    op = ProviderOperation(
        operation_type=ProviderOperationType.HEALTH_CHECK,
        parameters={},
    )
    result = await strategy.execute_operation(op)
    assert result.success is True
    assert result.data["is_healthy"] is True


@pytest.mark.asyncio
async def test_execute_operation_unsupported_returns_error() -> None:
    """Resource-lifecycle operations are NOT supported in Phase A."""
    from orb.providers.base.strategy import ProviderOperation

    strategy = _make_strategy()
    op = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={},
    )
    result = await strategy.execute_operation(op)
    assert result.success is False
    assert result.error_code == "UNSUPPORTED_OPERATION"


@pytest.mark.asyncio
async def test_typed_provisioning_methods_route_to_pod_handler() -> None:
    """Phase B routes typed entry points to the Pod handler when ``provider_api='KubernetesPod'``."""
    from orb.domain.base.operation_outcome import Accepted

    strategy = _make_strategy()

    fake_handler = MagicMock()
    fake_handler.acquire_hosts = AsyncMock(
        return_value={
            "resource_ids": ["orb-aaa-0000"],
            "machine_ids": ["orb-aaa-0000"],
            "provider_data": {"namespace": "default", "pod_names": ["orb-aaa-0000"]},
        }
    )
    fake_handler.release_hosts = AsyncMock(return_value=None)
    strategy._handlers["KubernetesPod"] = fake_handler  # type: ignore[attr-defined]

    fake_request = MagicMock()
    fake_request.request_id = "req-test"
    fake_request.provider_api = "KubernetesPod"
    fake_request.template_id = "tpl-1"
    fake_request.requested_count = 1
    fake_request.metadata = {}

    outcome = await strategy.acquire(fake_request)
    assert isinstance(outcome, Accepted)
    assert outcome.pending_resource_ids == ["orb-aaa-0000"]

    return_outcome = await strategy.return_machines(["orb-aaa-0000"], fake_request)
    assert isinstance(return_outcome, Accepted)
    assert return_outcome.pending_resource_ids == ["orb-aaa-0000"]


@pytest.mark.asyncio
async def test_unsupported_provider_api_returns_failed() -> None:
    """A provider_api that does not match any of the registered handlers
    (Pod / Deployment / StatefulSet / Job) is rejected via the
    :class:`Failed` outcome."""
    from orb.domain.base.operation_outcome import Failed

    strategy = _make_strategy()

    fake_request = MagicMock()
    fake_request.request_id = "req-test"
    # Unknown provider-API key — must NOT match any of the four
    # registered handler keys.
    fake_request.provider_api = "KubernetesUnknownApi"
    fake_request.template_id = "tpl-1"
    fake_request.requested_count = 1
    fake_request.metadata = {}

    outcome = await strategy.acquire(fake_request)
    assert isinstance(outcome, Failed)
    assert "not yet implemented" in outcome.error


def test_cleanup_idempotent() -> None:
    strategy = _make_strategy()
    strategy.cleanup()
    strategy.cleanup()
    assert strategy._kubernetes_client is None  # type: ignore[attr-defined]

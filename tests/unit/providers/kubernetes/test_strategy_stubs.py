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
from unittest.mock import MagicMock

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
async def test_typed_provisioning_methods_raise_not_implemented() -> None:
    """Phase A stubs every typed entry point with ``NotImplementedError``."""
    strategy = _make_strategy()
    fake_request = MagicMock()

    with pytest.raises(NotImplementedError):
        await strategy.acquire(fake_request)
    with pytest.raises(NotImplementedError):
        await strategy.return_machines(["m-1"], fake_request)
    with pytest.raises(NotImplementedError):
        await strategy.get_status(["m-1"], fake_request)


def test_cleanup_idempotent() -> None:
    strategy = _make_strategy()
    strategy.cleanup()
    strategy.cleanup()
    assert strategy._kubernetes_client is None  # type: ignore[attr-defined]

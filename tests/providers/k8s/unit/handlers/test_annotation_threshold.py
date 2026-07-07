"""T10 — deployment annotation 50%-threshold rewrite logic.

Verifies that the abort boundary for failed victim annotations uses
``math.ceil`` semantics so that exactly 50% failures abort the release
(never proceed below 50% successful annotations).

Test matrix for 10 victims (selective path — current_replicas=15 so
``len(machine_ids)=10 < 15=current_replicas`` keeps it on the selective path):
  - 4 failures / 10 = 40%  → below threshold (ceil(10/2)=5) → proceeds
  - 5 failures / 10 = 50%  → at threshold (ceil(10/2)=5) → ABORTS
  - 6 failures / 10 = 60%  → above threshold → ABORTS
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Use 15 current replicas so 10 victims stays on the SELECTIVE path
# (10 < 15 → full_release=False → annotation step runs).
_CURRENT_REPLICAS = 15
_VICTIM_COUNT = 10
_ALL_10 = [f"pod-{i}" for i in range(_VICTIM_COUNT)]


def _make_provider_data(
    *,
    deployment_name: str = "orb-deadbeef",
    namespace: str = "orb-test",
) -> dict[str, Any]:
    return {
        "request_id": f"req-{uuid.uuid4()}",
        "namespace": namespace,
        "deployment_name": deployment_name,
        "replicas": _CURRENT_REPLICAS,
    }


def _make_deployment_status(*, spec_replicas: int) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name="orb-deadbeef", namespace="orb-test"),
        spec=SimpleNamespace(replicas=spec_replicas),
        status=SimpleNamespace(
            available_replicas=spec_replicas,
            ready_replicas=spec_replicas,
            updated_replicas=spec_replicas,
            conditions=[],
        ),
    )


def _make_handler_with_failing_annotations(
    *, fail_pods: set[str]
) -> tuple[K8sDeploymentHandler, MagicMock]:
    from kubernetes.client.exceptions import ApiException

    core_v1 = MagicMock()

    def _patch(*, name: str, namespace: str, body: Any) -> None:
        if name in fail_pods:
            raise ApiException(status=503, reason="Service Unavailable")

    core_v1.patch_namespaced_pod.side_effect = _patch

    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = _make_deployment_status(
        spec_replicas=_CURRENT_REPLICAS,
    )
    apps_v1.patch_namespaced_deployment_scale.return_value = SimpleNamespace()

    k8s_client = MagicMock()
    k8s_client.core_v1 = core_v1
    k8s_client.apps_v1 = apps_v1

    config = K8sProviderConfig(namespace="orb-test")
    handler = K8sDeploymentHandler(
        kubernetes_client=k8s_client,
        config=config,
        logger=MagicMock(),
    )
    handler._max_retries = 1
    return handler, apps_v1


# ---------------------------------------------------------------------------
# T10 — boundary tests: 49%, 50%, 51% failure rate with 10 victims
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_annotation_4_of_10_failure_proceeds() -> None:
    """4/10 failures = 40% — below ceil(10/2)=5 threshold → release proceeds."""
    fail_pods = set(_ALL_10[:4])  # 4 failures
    handler, apps_v1 = _make_handler_with_failing_annotations(fail_pods=fail_pods)
    provider_data = _make_provider_data()

    # Must NOT raise; replicas should be patched.
    await handler.release_hosts(_ALL_10, provider_data)
    apps_v1.patch_namespaced_deployment_scale.assert_called_once()


@pytest.mark.asyncio
async def test_annotation_5_of_10_failure_aborts() -> None:
    """5/10 failures = exactly 50% — at ceil(10/2)=5 threshold → ABORT.

    This is the key boundary fix: the old ``> 0.5`` comparison allowed 5/10
    to proceed; ``math.ceil`` semantics now abort at exactly 50%.
    """
    fail_pods = set(_ALL_10[:5])  # 5 failures
    handler, apps_v1 = _make_handler_with_failing_annotations(fail_pods=fail_pods)
    provider_data = _make_provider_data()

    with pytest.raises(RuntimeError, match="Aborted selective release"):
        await handler.release_hosts(_ALL_10, provider_data)
    apps_v1.patch_namespaced_deployment_scale.assert_not_called()


@pytest.mark.asyncio
async def test_annotation_6_of_10_failure_aborts() -> None:
    """6/10 failures = 60% — above ceil(10/2)=5 threshold → ABORT."""
    fail_pods = set(_ALL_10[:6])  # 6 failures
    handler, apps_v1 = _make_handler_with_failing_annotations(fail_pods=fail_pods)
    provider_data = _make_provider_data()

    with pytest.raises(RuntimeError, match="Aborted selective release"):
        await handler.release_hosts(_ALL_10, provider_data)
    apps_v1.patch_namespaced_deployment_scale.assert_not_called()


@pytest.mark.asyncio
async def test_annotation_zero_failures_proceeds() -> None:
    """0 failures → threshold not reached → release proceeds."""
    handler, apps_v1 = _make_handler_with_failing_annotations(fail_pods=set())
    provider_data = _make_provider_data()

    await handler.release_hosts(_ALL_10, provider_data)
    apps_v1.patch_namespaced_deployment_scale.assert_called_once()

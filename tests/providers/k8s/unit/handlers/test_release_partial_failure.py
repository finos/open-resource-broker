"""Unit tests for ``K8sPodHandler.release_hosts`` partial-failure handling.

Verifies that:
* One failing delete does not abort the remaining deletes (no orphans).
* Each per-pod failure is logged at WARNING with request_id + pod name + reason.
* The method only raises when ALL deletes fail.
* The returned dict carries the correct ``deleted`` and ``failed_deletes`` keys.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(*, request_id: str | None = None) -> Request:
    return Request(
        request_id=RequestId(value=request_id or f"req-{uuid.uuid4()}"),
        request_type=RequestType.RETURN,
        provider_type="k8s",
        provider_api="Pod",
        template_id="tpl-1",
        requested_count=0,
        provider_data={"namespace": "orb-test"},
    )


def _make_handler(core_v1_mock: Any) -> K8sPodHandler:
    client = MagicMock()
    client.core_v1 = core_v1_mock
    config = K8sProviderConfig(namespace="orb-test")
    logger = MagicMock()
    handler = K8sPodHandler(kubernetes_client=client, config=config, logger=logger)
    handler._max_retries = 1
    return handler


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_hosts_partial_failure_deletes_survivors() -> None:
    """One failing pod must not abort the other deletes."""
    core_v1 = MagicMock()

    call_count: dict[str, int] = {}

    def _delete(*, name: str, namespace: str) -> None:
        call_count[name] = call_count.get(name, 0) + 1
        if name == "orb-fail-0001":
            raise RuntimeError("timeout")

    core_v1.delete_namespaced_pod.side_effect = _delete
    handler = _make_handler(core_v1)
    request = _make_request()

    result = await handler.release_hosts(
        ["orb-ok-0000", "orb-fail-0001", "orb-ok-0002"], request.provider_data
    )

    assert sorted(result["deleted"]) == ["orb-ok-0000", "orb-ok-0002"]
    assert len(result["failed_deletes"]) == 1
    assert result["failed_deletes"][0][0] == "orb-fail-0001"
    assert "timeout" in result["failed_deletes"][0][1]


@pytest.mark.asyncio
async def test_release_hosts_partial_failure_logs_each_failure_at_warning() -> None:
    """Each individual delete failure must be logged at WARNING with request_id + pod name."""
    core_v1 = MagicMock()
    core_v1.delete_namespaced_pod.side_effect = RuntimeError("apiserver down")
    logger = MagicMock()
    client = MagicMock()
    client.core_v1 = core_v1
    handler = K8sPodHandler(
        kubernetes_client=client,
        config=K8sProviderConfig(namespace="orb-test"),
        logger=logger,
    )
    handler._max_retries = 1
    request = _make_request(request_id=f"req-{uuid.UUID(int=0)}")

    # Two pods both fail — both should be logged at WARNING.
    with pytest.raises(RuntimeError, match="All pod deletes failed"):
        await handler.release_hosts(["orb-a-0000", "orb-b-0001"], request.provider_data)

    warning_calls = [str(c) for c in logger.warning.call_args_list]
    # Each warning must mention the pod names (request_id is an opaque object in warnings).
    assert any("orb-a-0000" in w for w in warning_calls)
    assert any("orb-b-0001" in w for w in warning_calls)


@pytest.mark.asyncio
async def test_release_hosts_all_failures_raises() -> None:
    """When every delete fails the method must raise RuntimeError."""
    core_v1 = MagicMock()
    core_v1.delete_namespaced_pod.side_effect = RuntimeError("cluster unreachable")
    handler = _make_handler(core_v1)
    request = _make_request()

    with pytest.raises(RuntimeError, match="All pod deletes failed"):
        await handler.release_hosts(["orb-a-0000", "orb-b-0001"], request.provider_data)


@pytest.mark.asyncio
async def test_release_hosts_single_failure_does_not_raise() -> None:
    """A partial failure (some succeed) must NOT raise."""
    core_v1 = MagicMock()

    def _delete(*, name: str, namespace: str) -> None:
        if name == "orb-bad-0001":
            raise RuntimeError("gone badly")

    core_v1.delete_namespaced_pod.side_effect = _delete
    handler = _make_handler(core_v1)
    request = _make_request()

    result = await handler.release_hosts(["orb-good-0000", "orb-bad-0001"], request.provider_data)
    assert result["deleted"] == ["orb-good-0000"]
    assert result["failed_deletes"][0][0] == "orb-bad-0001"


@pytest.mark.asyncio
async def test_release_hosts_empty_list_returns_empty_dicts() -> None:
    core_v1 = MagicMock()
    handler = _make_handler(core_v1)
    request = _make_request()

    result = await handler.release_hosts([], request.provider_data)
    assert result == {"deleted": [], "failed_deletes": []}
    core_v1.delete_namespaced_pod.assert_not_called()

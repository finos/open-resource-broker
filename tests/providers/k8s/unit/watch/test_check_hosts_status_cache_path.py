"""Cache-first read path for :meth:`K8sPodHandler.check_hosts_status`.

Validates the cache-aware behaviour:

* When a :class:`PodStateCache` is injected and the watcher reports
  alive, the handler reads from the cache without calling
  ``list_namespaced_pod``.
* When the watcher reports dead, the cache is ignored and the handler
  falls back to the on-demand list path.
* When the cache has no entry for the request (cold start) the handler
  also falls back.
* Stale entries (older than the configured timeout) are evicted and
  the handler falls back.
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.handlers.pod_handler import K8sPodHandler
from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(*, requested_count: int = 2) -> Request:
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data={"namespace": "orb-test"},
    )


def _build_handler(
    *,
    cache: PodStateCache,
    cache_alive: bool,
    stale_cache_timeout_seconds: float = 600.0,
    core_v1_side_effect: Any = None,
) -> tuple[K8sPodHandler, MagicMock]:
    client = MagicMock()
    core_v1 = MagicMock()
    if core_v1_side_effect is not None:
        core_v1.list_namespaced_pod.side_effect = core_v1_side_effect
    else:
        core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    client.core_v1 = core_v1
    config = K8sProviderConfig(namespace="orb-test")
    handler = K8sPodHandler(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
        pod_state_cache=cache,
        cache_alive=lambda alive=cache_alive: alive,
        stale_cache_timeout_seconds=stale_cache_timeout_seconds,
    )
    return handler, core_v1


def _seed_cache_for(cache: PodStateCache, request: Request, *, count: int, status: str) -> None:
    for i in range(count):
        cache.upsert(
            PodState(
                request_id=str(request.request_id),
                pod_name=f"pod-{i}",
                namespace="orb-test",
                status=status,
                phase="Running" if status == "running" else "Pending",
                ready=status == "running",
            )
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cache_hit_skips_list_call() -> None:
    """Alive watcher + cache hit must serve the read from memory."""
    cache = PodStateCache()
    request = _make_request(requested_count=2)
    _seed_cache_for(cache, request, count=2, status="running")
    handler, core_v1 = _build_handler(cache=cache, cache_alive=True)

    result = handler.check_hosts_status(request)

    core_v1.list_namespaced_pod.assert_not_called()
    assert result.fulfilment.state == "fulfilled"
    assert result.fulfilment.running_count == 2
    assert len(result.instances) == 2
    inst = result.instances[0]
    assert inst["status"] == "running"
    assert inst["provider_api"] == "Pod"
    assert inst["provider_data"]["namespace"] == "orb-test"
    assert inst["provider_data"]["phase"] == "Running"
    assert inst["provider_data"]["ready"] is True


def test_cache_disabled_falls_back_to_list() -> None:
    """``cache_alive() is False`` must bypass the cache entirely."""
    cache = PodStateCache()
    request = _make_request(requested_count=2)
    _seed_cache_for(cache, request, count=2, status="running")
    handler, core_v1 = _build_handler(cache=cache, cache_alive=False)

    handler.check_hosts_status(request)

    core_v1.list_namespaced_pod.assert_called_once()


def test_cache_miss_falls_back_to_list() -> None:
    """Cold-start (no entry for request) must trigger an on-demand list."""
    cache = PodStateCache()
    request = _make_request(requested_count=2)
    handler, core_v1 = _build_handler(cache=cache, cache_alive=True)

    handler.check_hosts_status(request)

    core_v1.list_namespaced_pod.assert_called_once()


def test_stale_entries_are_dropped_and_fall_back() -> None:
    """Cached entries older than the stale timeout are evicted before read."""
    cache = PodStateCache()
    request = _make_request(requested_count=2)
    _seed_cache_for(cache, request, count=2, status="running")
    time.sleep(0.02)
    handler, core_v1 = _build_handler(
        cache=cache,
        cache_alive=True,
        stale_cache_timeout_seconds=0.005,
    )

    handler.check_hosts_status(request)

    core_v1.list_namespaced_pod.assert_called_once()
    assert cache.get(str(request.request_id)) is None


@pytest.mark.parametrize(
    "status,expected_state",
    [
        ("running", "fulfilled"),
        ("pending", "in_progress"),
        ("starting", "in_progress"),
    ],
)
def test_cache_hit_fulfilment_states(status: str, expected_state: str) -> None:
    """Cache-path fulfilment math must match the list-path semantics."""
    cache = PodStateCache()
    request = _make_request(requested_count=2)
    _seed_cache_for(cache, request, count=2, status=status)
    handler, _ = _build_handler(cache=cache, cache_alive=True)

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == expected_state


def test_handler_without_cache_uses_list_path() -> None:
    """Constructed without a cache, the handler keeps the on-demand list behaviour."""
    client = MagicMock()
    client.core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    config = K8sProviderConfig(namespace="orb-test")
    handler = K8sPodHandler(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
    )
    handler.check_hosts_status(_make_request(requested_count=1))
    client.core_v1.list_namespaced_pod.assert_called_once()

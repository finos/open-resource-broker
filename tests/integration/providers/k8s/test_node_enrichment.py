"""Integration test: node metadata is surfaced in provider_data.

Exercises the path where a pod is created on a node that has labels
carrying instance-type, zone, and capacity-type metadata.  After
the node is upserted into the K8sNodeStateCache, get_status should
return per-instance dicts whose ``provider_data`` carries the three
node enrichment fields (``node_instance_type``, ``node_zone``,
``node_capacity_type``).

This test exercises both the list-fed path
(:meth:`K8sHandlerBase._instance_dict_for_pod`) and the cache-fed path
(:meth:`K8sHandlerBase._instance_dict_for_state`) so both code paths
are covered in a single scenario.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy
from orb.providers.k8s.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.k8s.watch.node_state_cache import K8sNodeState, K8sNodeStateCache
from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache

from .conftest import make_namespaced_config, make_pod_object, make_request, make_template

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


NODE_NAME = "node-worker-1"
NODE_INSTANCE_TYPE = "c5.4xlarge"
NODE_ZONE = "us-east-1b"
NODE_CAPACITY_TYPE = "spot"


def _node_state(name: str = NODE_NAME) -> K8sNodeState:
    return K8sNodeState(
        name=name,
        instance_type=NODE_INSTANCE_TYPE,
        zone=NODE_ZONE,
        capacity_type=NODE_CAPACITY_TYPE,
        cpu_capacity="16",
        memory_capacity="32Gi",
        cpu_allocatable="15500m",
        memory_allocatable="30Gi",
        conditions=[
            {
                "type": "Ready",
                "status": "True",
                "reason": None,
                "lastTransitionTime": "",
            }
        ],
        ready=True,
    )


def _make_strategy_with_node_cache(
    *,
    client: MagicMock,
    node_cache: K8sNodeStateCache,
    pod_cache: PodStateCache,
    config: K8sProviderConfig | None = None,
) -> K8sProviderStrategy:
    """Build a strategy with both a pod cache and a pre-seeded node cache."""
    cfg = config or make_namespaced_config()
    watch_manager = MultiNamespaceWatcher(
        kubernetes_client=client,
        config=cfg,
        logger=MagicMock(),
        cache=pod_cache,
    )
    strategy = K8sProviderStrategy(
        config=cfg,
        logger=MagicMock(),
        kubernetes_client=client,
        watch_manager=watch_manager,
        node_state_cache=node_cache,
    )
    client.core_v1.get_api_resources.return_value = SimpleNamespace(
        group_version="v1", resources=[object(), object()]
    )
    assert strategy.initialize() is True
    return strategy


# ---------------------------------------------------------------------------
# List-fed path: pod objects from CoreV1Api.list_namespaced_pod
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_fed_get_status_includes_node_metadata() -> None:
    """Node metadata appears in provider_data when resolved via list call."""
    node_cache = K8sNodeStateCache()
    node_cache.upsert(_node_state())

    pod_cache = PodStateCache()  # Empty — forces list fallback
    client = MagicMock()

    request = make_request(requested_count=1, namespace="orb-it")
    template = make_template()
    request.metadata["template"] = template

    # Build a pod object assigned to NODE_NAME
    pod = make_pod_object(
        name="orb-abc-0001",
        namespace="orb-it",
        request_id=str(request.request_id),
    )
    # Override the node assignment
    pod.spec = SimpleNamespace(node_name=NODE_NAME)

    client.core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

    strategy = _make_strategy_with_node_cache(
        client=client,
        node_cache=node_cache,
        pod_cache=pod_cache,
    )

    outcome = await strategy.get_status(["orb-abc-0001"], request)
    instances: list[dict[str, Any]] = outcome.metadata.get("instances", [])

    assert len(instances) == 1
    pd = instances[0]["provider_data"]
    assert pd.get("node_instance_type") == NODE_INSTANCE_TYPE
    assert pd.get("node_zone") == NODE_ZONE
    assert pd.get("node_capacity_type") == NODE_CAPACITY_TYPE


# ---------------------------------------------------------------------------
# Cache-fed path: states from PodStateCache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_fed_get_status_includes_node_metadata() -> None:
    """Node metadata appears in provider_data when resolved via pod state cache."""
    node_cache = K8sNodeStateCache()
    node_cache.upsert(_node_state())

    pod_cache = PodStateCache()
    request = make_request(requested_count=1, namespace="orb-it")
    template = make_template()
    request.metadata["template"] = template
    request_id = str(request.request_id)

    # Seed the pod cache directly
    pod_cache.upsert(
        PodState(
            request_id=request_id,
            pod_name="orb-abc-0001",
            namespace="orb-it",
            status="running",
            phase="Running",
            ready=True,
            node_name=NODE_NAME,
        )
    )

    client = MagicMock()
    # list_namespaced_pod should NOT be called — cache hit
    client.core_v1.list_namespaced_pod.side_effect = AssertionError(
        "list_namespaced_pod must not be called on cache hit"
    )

    strategy = _make_strategy_with_node_cache(
        client=client,
        node_cache=node_cache,
        pod_cache=pod_cache,
    )
    # Mark the watcher as "healthy" so the cache path is taken.
    assert strategy._watch_manager is not None
    strategy._watch_manager._started = True
    for w in strategy._watch_manager._watchers:
        w._task = MagicMock()
        w._task.done.return_value = False

    # Wire a fresh watcher to avoid test interference when no watchers list.
    # We need the watch_manager.is_healthy() == True path, so we create a
    # small helper approach: force is_healthy to return True via the watcher
    # being running.
    #
    # Simpler: pre-wire the watch_manager watchers list with a mock watcher.
    from orb.providers.k8s.watch.watcher import K8sWatcher

    mock_watcher = MagicMock(spec=K8sWatcher)
    mock_watcher.is_running.return_value = True
    strategy._watch_manager._watchers = [mock_watcher]
    strategy._watch_manager._started = True
    # is_healthy() gates on _first_sync_complete since the reconciler-driven
    # cold-start fix — mark it here so the cache-fed code path is taken.
    strategy._watch_manager.mark_first_sync_complete()

    outcome = await strategy.get_status(["orb-abc-0001"], request)
    instances: list[dict[str, Any]] = outcome.metadata.get("instances", [])

    assert len(instances) == 1
    pd = instances[0]["provider_data"]
    assert pd.get("node_instance_type") == NODE_INSTANCE_TYPE
    assert pd.get("node_zone") == NODE_ZONE
    assert pd.get("node_capacity_type") == NODE_CAPACITY_TYPE


# ---------------------------------------------------------------------------
# No-op when node not in cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_node_in_cache_does_not_add_fields() -> None:
    """provider_data must not have node_* keys when node is absent from cache."""
    node_cache = K8sNodeStateCache()  # Empty — no node info
    pod_cache = PodStateCache()
    client = MagicMock()

    request = make_request(requested_count=1, namespace="orb-it")
    template = make_template()
    request.metadata["template"] = template

    pod = make_pod_object(
        name="orb-abc-0001",
        namespace="orb-it",
        request_id=str(request.request_id),
    )
    pod.spec = SimpleNamespace(node_name="some-untracked-node")
    client.core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

    strategy = _make_strategy_with_node_cache(
        client=client,
        node_cache=node_cache,
        pod_cache=pod_cache,
    )

    outcome = await strategy.get_status(["orb-abc-0001"], request)
    instances: list[dict[str, Any]] = outcome.metadata.get("instances", [])

    assert len(instances) == 1
    pd = instances[0]["provider_data"]
    # None of the node enrichment keys should be present
    assert "node_instance_type" not in pd
    assert "node_zone" not in pd
    assert "node_capacity_type" not in pd


# ---------------------------------------------------------------------------
# node_watch_enabled=False (default) — no enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_watch_disabled_no_node_cache_wired() -> None:
    """When node_watch_enabled=False the node_state_cache is empty by default."""
    cfg = make_namespaced_config()
    assert cfg.node_watch_enabled is False

    pod_cache = PodStateCache()
    client = MagicMock()
    watch_manager = MultiNamespaceWatcher(
        kubernetes_client=client, config=cfg, logger=MagicMock(), cache=pod_cache
    )
    strategy = K8sProviderStrategy(
        config=cfg,
        logger=MagicMock(),
        kubernetes_client=client,
        watch_manager=watch_manager,
    )
    client.core_v1.get_api_resources.return_value = SimpleNamespace(
        group_version="v1", resources=[]
    )
    strategy.initialize()

    # The strategy must have an empty (not None) node_state_cache.
    assert strategy.node_state_cache is not None
    assert strategy.node_state_cache.size() == 0

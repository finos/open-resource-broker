"""Orphan GC sweep of custom resources declared by plugin handlers.

A plugin handler declares the custom resource kinds it creates through
``register_k8s_handler(custom_resources=...)``.  The GC must treat those
objects exactly like pods: only this installation's objects, nothing on an
empty or failed request lookup, min-age and re-check before every delete.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.handler_plugins import (
    K8sCustomResource,
    register_k8s_handler,
    unregister_k8s_handler,
)
from orb.providers.k8s.reconciliation.orphan_gc import (
    OrphanCustomResource,
    OrphanGarbageCollector,
)

_SANDBOXES = K8sCustomResource("sandbox.example.io", "v1alpha1", "sandboxes")
_OLD = "2026-06-19T11:00:00Z"


def _obj(
    *,
    name: str,
    request_id: str | None,
    namespace: str = "orb",
    instance: str = "orb-a",
    creation_timestamp: str = _OLD,
    deleting: bool = False,
) -> dict[str, Any]:
    labels = {"orb.io/managed": "true", "orb.io/orb-instance": instance}
    if request_id is not None:
        labels["orb.io/request-id"] = request_id
    metadata: dict[str, Any] = {
        "name": name,
        "namespace": namespace,
        "labels": labels,
        "creationTimestamp": creation_timestamp,
    }
    if deleting:
        metadata["deletionTimestamp"] = "2026-06-19T12:00:00Z"
    return {"apiVersion": "sandbox.example.io/v1alpha1", "kind": "Sandbox", "metadata": metadata}


def _make_gc(
    *,
    objects: list[dict[str, Any]] | None = None,
    known: list[str] | None = None,
    existing: list[str] | None = None,
    list_raises: Exception | None = None,
    delete_raises: Exception | None = None,
    resources: list[K8sCustomResource] | None = None,
    **config: Any,
) -> tuple[OrphanGarbageCollector, MagicMock, MagicMock]:
    client = MagicMock()
    client.core_v1.list_namespaced_pod.return_value = MagicMock(items=[])
    client.core_v1.list_pod_for_all_namespaces.return_value = MagicMock(items=[])
    custom = client.custom_objects_api
    if list_raises is not None:
        custom.list_namespaced_custom_object.side_effect = list_raises
        custom.list_cluster_custom_object.side_effect = list_raises
    else:
        custom.list_namespaced_custom_object.return_value = {"items": list(objects or [])}
        custom.list_cluster_custom_object.return_value = {"items": list(objects or [])}
    if delete_raises is not None:
        custom.delete_namespaced_custom_object.side_effect = delete_raises

    settings: dict[str, Any] = {
        "orb_instance_id": "orb-a",
        "namespace": "orb",
        "orphan_min_age_seconds": 0,
    }
    settings.update(config)
    logger = MagicMock()
    gc = OrphanGarbageCollector(
        kubernetes_client=client,
        config=K8sProviderConfig(**settings),
        logger=logger,
        known_request_ids=lambda: list(known if known is not None else ["req-live"]),
        existing_request_ids=(
            (lambda ids: [i for i in ids if i in existing]) if existing is not None else None
        ),
        custom_resources=(lambda: list(resources if resources is not None else [_SANDBOXES])),
    )
    return gc, custom, logger


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reports_an_orphan_without_deleting_by_default() -> None:
    gc, custom, logger = _make_gc(
        objects=[
            _obj(name="sb-live", request_id="req-live"),
            _obj(name="sb-gone", request_id="req-gone"),
        ]
    )
    orphans = await gc.run_once()
    assert orphans == [
        OrphanCustomResource(
            resource=_SANDBOXES,
            name="sb-gone",
            namespace="orb",
            request_id="req-gone",
            creation_timestamp=_OLD,
        )
    ]
    custom.delete_namespaced_custom_object.assert_not_called()
    assert gc.stats.last_orphans_found == 1
    assert gc.stats.custom_resources_checked == ["sandboxes.sandbox.example.io/v1alpha1"]
    assert any("Orphan custom resource detected" in str(c) for c in logger.warning.call_args_list)


@pytest.mark.asyncio
async def test_lists_with_this_installations_selector() -> None:
    gc, custom, _ = _make_gc(objects=[])
    await gc.run_once()
    custom.list_namespaced_custom_object.assert_called_once()
    kwargs = custom.list_namespaced_custom_object.call_args.kwargs
    assert kwargs["group"] == "sandbox.example.io"
    assert kwargs["version"] == "v1alpha1"
    assert kwargs["plural"] == "sandboxes"
    assert kwargs["namespace"] == "orb"
    assert kwargs["label_selector"] == "orb.io/managed=true,orb.io/orb-instance=orb-a"


@pytest.mark.asyncio
async def test_cluster_wide_mode_lists_across_namespaces() -> None:
    gc, custom, _ = _make_gc(
        objects=[_obj(name="sb", request_id="req-gone", namespace="team-a")], namespaces=["*"]
    )
    orphans = await gc.run_once()
    custom.list_cluster_custom_object.assert_called_once()
    custom.list_namespaced_custom_object.assert_not_called()
    assert [(o.namespace, o.name) for o in orphans] == [("team-a", "sb")]


@pytest.mark.asyncio
async def test_ignores_objects_of_another_installation() -> None:
    gc, _, _ = _make_gc(objects=[_obj(name="sb", request_id="req-gone", instance="orb-b")])
    assert await gc.run_once() == []


@pytest.mark.asyncio
async def test_ignores_objects_already_being_deleted() -> None:
    gc, _, _ = _make_gc(objects=[_obj(name="sb", request_id="req-gone", deleting=True)])
    assert await gc.run_once() == []


@pytest.mark.asyncio
async def test_object_without_request_id_is_reported_but_never_deleted() -> None:
    gc, custom, _ = _make_gc(objects=[_obj(name="sb", request_id=None)], auto_cleanup_orphans=True)
    assert await gc.run_once() == []
    assert gc.stats.skipped_unlabeled == 1
    custom.delete_namespaced_custom_object.assert_not_called()


@pytest.mark.parametrize("known", [[], None])
@pytest.mark.asyncio
async def test_empty_request_store_lists_nothing(known: list[str] | None) -> None:
    gc, custom, _ = _make_gc(
        objects=[_obj(name="sb", request_id="req-gone")], auto_cleanup_orphans=True
    )
    gc._known_request_ids_fn = (lambda: []) if known == [] else None
    assert await gc.run_once() == []
    custom.list_namespaced_custom_object.assert_not_called()
    custom.delete_namespaced_custom_object.assert_not_called()


@pytest.mark.asyncio
async def test_no_instance_id_lists_nothing() -> None:
    gc, custom, _ = _make_gc(
        objects=[_obj(name="sb", request_id="req-gone")],
        orb_instance_id=None,
        auto_cleanup_orphans=True,
    )
    assert await gc.run_once() == []
    custom.list_namespaced_custom_object.assert_not_called()


@pytest.mark.asyncio
async def test_missing_crd_is_skipped_and_warned_once() -> None:
    gc, custom, logger = _make_gc(list_raises=ApiException(status=404))
    assert await gc.run_once() == []
    assert await gc.run_once() == []
    warnings = [c for c in logger.warning.call_args_list if "not served by the cluster" in str(c)]
    assert len(warnings) == 1
    assert gc.stats.last_error is None


@pytest.mark.asyncio
async def test_list_failure_is_recorded_and_pods_still_swept() -> None:
    gc, _, _ = _make_gc(list_raises=ApiException(status=500))
    assert await gc.run_once() == []
    assert gc.stats.last_error is not None
    assert "sandboxes.sandbox.example.io/v1alpha1" in gc.stats.last_error
    gc._client.core_v1.list_namespaced_pod.assert_called_once()


@pytest.mark.asyncio
async def test_failing_resource_lookup_sweeps_pods_only() -> None:
    gc, custom, _ = _make_gc()

    def _boom() -> list[K8sCustomResource]:
        raise RuntimeError("registry unavailable")

    gc._custom_resources_fn = _boom
    assert await gc.run_once() == []
    custom.list_namespaced_custom_object.assert_not_called()
    gc._client.core_v1.list_namespaced_pod.assert_called_once()


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_cleanup_deletes_the_orphan() -> None:
    gc, custom, _ = _make_gc(
        objects=[_obj(name="sb-gone", request_id="req-gone")], auto_cleanup_orphans=True
    )
    await gc.run_once()
    custom.delete_namespaced_custom_object.assert_called_once_with(
        group="sandbox.example.io",
        version="v1alpha1",
        namespace="orb",
        plural="sandboxes",
        name="sb-gone",
    )
    assert gc.stats.total_orphans_deleted == 1


@pytest.mark.asyncio
async def test_recheck_keeps_an_object_whose_request_reappeared() -> None:
    gc, custom, _ = _make_gc(
        objects=[_obj(name="sb", request_id="req-new")],
        existing=["req-new"],
        auto_cleanup_orphans=True,
    )
    await gc.run_once()
    custom.delete_namespaced_custom_object.assert_not_called()
    assert gc.stats.skipped_recheck == 1


@pytest.mark.asyncio
async def test_young_object_is_not_deleted() -> None:
    young = (
        datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(seconds=5)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    gc, custom, _ = _make_gc(
        objects=[_obj(name="sb", request_id="req-gone", creation_timestamp=young)],
        auto_cleanup_orphans=True,
        orphan_min_age_seconds=300,
    )
    await gc.run_once()
    custom.delete_namespaced_custom_object.assert_not_called()


@pytest.mark.asyncio
async def test_delete_not_found_counts_as_deleted() -> None:
    gc, _, _ = _make_gc(
        objects=[_obj(name="sb", request_id="req-gone")],
        delete_raises=ApiException(status=404),
        auto_cleanup_orphans=True,
    )
    await gc.run_once()
    assert gc.stats.total_orphans_deleted == 1
    assert gc.stats.delete_failures == 0


@pytest.mark.asyncio
async def test_delete_failure_is_counted() -> None:
    gc, _, _ = _make_gc(
        objects=[_obj(name="sb", request_id="req-gone")],
        delete_raises=ApiException(status=403),
        auto_cleanup_orphans=True,
    )
    await gc.run_once()
    assert gc.stats.delete_failures == 1
    assert gc.stats.total_orphans_deleted == 0


# ---------------------------------------------------------------------------
# Default source: the plugin registry
# ---------------------------------------------------------------------------


class _StubHandler:
    """Never constructed here."""


@pytest.fixture
def _registered_plugin() -> Iterator[None]:
    register_k8s_handler("KubernetesSandboxTest", _StubHandler, custom_resources=[_SANDBOXES])
    yield
    unregister_k8s_handler("KubernetesSandboxTest")


@pytest.mark.usefixtures("_registered_plugin")
@pytest.mark.asyncio
async def test_sweeps_kinds_declared_through_register_k8s_handler() -> None:
    client = MagicMock()
    client.core_v1.list_namespaced_pod.return_value = MagicMock(items=[])
    client.custom_objects_api.list_namespaced_custom_object.return_value = {
        "items": [_obj(name="sb", request_id="req-gone")]
    }
    gc = OrphanGarbageCollector(
        kubernetes_client=client,
        config=K8sProviderConfig(orb_instance_id="orb-a", namespace="orb"),
        logger=MagicMock(),
        known_request_ids=lambda: ["req-live"],
    )
    orphans = await gc.run_once()
    assert [o.name for o in orphans if isinstance(o, OrphanCustomResource)] == ["sb"]


@pytest.mark.asyncio
async def test_no_declared_kinds_never_touches_custom_objects() -> None:
    gc, custom, _ = _make_gc(resources=[])
    await gc.run_once()
    custom.list_namespaced_custom_object.assert_not_called()
    custom.list_cluster_custom_object.assert_not_called()

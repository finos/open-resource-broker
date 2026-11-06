"""Orphan GC and startup reconciler ownership rules, driven through ``create_k8s_strategy``.

The strategy is built by the real registration factory so the request-id
lookups wired there are the ones under test.  Only the Kubernetes API (a small
fake that honours label selectors) and the DI container's request repository
are substituted.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any, Callable, Optional
from unittest.mock import MagicMock, patch

import pytest

from orb.domain.request.repository import RequestRepository
from orb.providers.k8s.registration import create_k8s_strategy
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

INSTANCE = "orb-a"
OLD = "2020-01-01T00:00:00Z"


def _pod(
    name: str,
    request_id: Optional[str],
    *,
    instance: Optional[str] = INSTANCE,
    managed: bool = True,
) -> SimpleNamespace:
    labels: dict[str, str] = {}
    if managed:
        labels["orb.io/managed"] = "true"
    if request_id is not None:
        labels["orb.io/request-id"] = request_id
    if instance is not None:
        labels["orb.io/orb-instance"] = instance
    metadata = SimpleNamespace(
        name=name, namespace="orb", labels=labels, creation_timestamp=OLD, uid=name
    )
    return SimpleNamespace(metadata=metadata, status=SimpleNamespace(phase="Running"), spec=None)


class _FakeCoreV1:
    """Pod API that filters by equality label selectors like the apiserver does."""

    def __init__(self, pods: list[SimpleNamespace]) -> None:
        self.pods = pods
        self.deleted: list[str] = []
        self.on_list: Optional[Callable[[], None]] = None

    def list_namespaced_pod(self, namespace: str, label_selector: str = "", **_: Any):
        if self.on_list is not None:
            self.on_list()
        wanted = dict(part.split("=", 1) for part in label_selector.split(",") if part)
        items = [
            p
            for p in self.pods
            if p.metadata.namespace == namespace
            and all(p.metadata.labels.get(k) == v for k, v in wanted.items())
        ]
        return SimpleNamespace(items=items)

    def delete_namespaced_pod(self, name: str, namespace: str, **_: Any) -> None:
        assert namespace == "orb"
        self.deleted.append(name)


class _FakeRepo:
    def __init__(self, request_ids: list[str], *, fail: bool = False) -> None:
        self.request_ids = list(request_ids)
        self.fail = fail

    def _req(self, rid: str) -> SimpleNamespace:
        return SimpleNamespace(request_id=rid)

    def find_all(self) -> list[SimpleNamespace]:
        if self.fail:
            raise RuntimeError("storage unavailable")
        return [self._req(r) for r in self.request_ids]

    def find_by_ids(self, ids: list[str]) -> list[SimpleNamespace]:
        if self.fail:
            raise RuntimeError("storage unavailable")
        return [self._req(r) for r in ids if r in self.request_ids]


@contextlib.contextmanager
def _container(repo: _FakeRepo):
    container = MagicMock()
    container.get.side_effect = lambda port: repo if port is RequestRepository else MagicMock()
    with patch("orb.infrastructure.di.container.get_container", return_value=container):
        yield


def _strategy(
    pods: list[SimpleNamespace], **overrides: Any
) -> tuple[K8sProviderStrategy, _FakeCoreV1]:
    cfg: dict[str, Any] = {
        "namespace": "orb",
        "in_cluster": True,
        "orphan_gc_enabled": True,
        "auto_cleanup_orphans": True,
        "orphan_min_age_seconds": 0,
        "orphan_gc_interval_seconds": 3600,
        "orb_instance_id": INSTANCE,
    }
    cfg.update(overrides)
    strategy = create_k8s_strategy(cfg)
    assert isinstance(strategy, K8sProviderStrategy)
    core = _FakeCoreV1(pods)
    strategy._kubernetes_client = SimpleNamespace(core_v1=core)  # type: ignore[assignment]
    return strategy, core


def _drop_lookup(lifecycle: Any) -> None:
    """Simulate a strategy built without any request-id lookup."""
    setattr(lifecycle, "_known_request_ids_fn", None)  # noqa: B010


async def _sweep(strategy: K8sProviderStrategy):
    """Run exactly one GC sweep built by the strategy's own lifecycle wiring."""
    lifecycle = strategy._reconciliation_lifecycle  # type: ignore[attr-defined]
    lifecycle.maybe_start_orphan_gc()
    gc = lifecycle.orphan_gc
    if gc is None:
        return None
    task = gc._task
    assert task is not None
    task.cancel()
    await asyncio.wait({task})
    await gc.run_once()
    return gc


@pytest.mark.asyncio
async def test_orphan_deleted_and_owned_pod_kept():
    pods = [_pod("owned", "req-1"), _pod("orphan", "req-gone")]
    with _container(_FakeRepo(["req-1"])):
        strategy, core = _strategy(pods)
        await _sweep(strategy)
    assert core.deleted == ["orphan"]


@pytest.mark.asyncio
async def test_pod_of_a_completed_request_is_kept():
    # Known ids cover every stored request, not only active ones.
    pods = [_pod("done", "req-completed")]
    with _container(_FakeRepo(["req-completed", "req-other"])):
        strategy, core = _strategy(pods)
        await _sweep(strategy)
    assert core.deleted == []


@pytest.mark.asyncio
async def test_empty_request_store_deletes_nothing():
    pods = [_pod("p1", "req-1"), _pod("p2", "req-2")]
    with _container(_FakeRepo([])):
        strategy, core = _strategy(pods)
        gc = await _sweep(strategy)
    assert core.deleted == []
    assert gc is not None and gc.stats.last_error


@pytest.mark.asyncio
async def test_request_store_read_failure_deletes_nothing():
    pods = [_pod("p1", "req-1")]
    with _container(_FakeRepo(["req-1"], fail=True)):
        strategy, core = _strategy(pods)
        await _sweep(strategy)
    assert core.deleted == []


@pytest.mark.asyncio
async def test_container_unavailable_deletes_nothing():
    pods = [_pod("p1", "req-1")]
    with patch(
        "orb.infrastructure.di.container.get_container", side_effect=RuntimeError("not ready")
    ):
        strategy, core = _strategy(pods)
        await _sweep(strategy)
    assert core.deleted == []


@pytest.mark.asyncio
async def test_missing_lookup_does_not_start_gc():
    with patch("orb.infrastructure.di.container.get_container", side_effect=RuntimeError):
        strategy, core = _strategy([_pod("p1", "req-x")])
    lifecycle = strategy._reconciliation_lifecycle  # type: ignore[attr-defined]
    _drop_lookup(lifecycle)
    lifecycle.maybe_start_orphan_gc()
    assert lifecycle.orphan_gc is None
    assert core.deleted == []


@pytest.mark.asyncio
async def test_unlabeled_managed_pod_is_kept():
    pods = [_pod("no-rid", None), _pod("blank-rid", ""), _pod("orphan", "req-gone")]
    with _container(_FakeRepo(["req-1"])):
        strategy, core = _strategy(pods)
        await _sweep(strategy)
    assert core.deleted == ["orphan"]


@pytest.mark.asyncio
async def test_other_instance_and_pre_upgrade_pods_are_kept():
    pods = [
        _pod("theirs", "req-gone", instance="orb-b"),
        _pod("pre-upgrade", "req-gone", instance=None),
        _pod("ours", "req-gone"),
    ]
    with _container(_FakeRepo(["req-1"])):
        strategy, core = _strategy(pods)
        await _sweep(strategy)
    assert core.deleted == ["ours"]


@pytest.mark.asyncio
async def test_gc_refuses_to_delete_without_instance_id():
    pods = [_pod("orphan", "req-gone", instance=None)]
    with _container(_FakeRepo(["req-1"])):
        strategy, core = _strategy(pods, orb_instance_id=None)
        gc = await _sweep(strategy)
    assert gc is None
    assert core.deleted == []


@pytest.mark.asyncio
async def test_request_saved_between_id_read_and_delete_is_kept():
    repo = _FakeRepo(["req-1"])
    pods = [_pod("late", "req-late")]
    with _container(repo):
        strategy, core = _strategy(pods)
        # The pod listing happens after the id read; the request commits then.
        core.on_list = lambda: repo.request_ids.append("req-late")
        await _sweep(strategy)
    assert core.deleted == []


@pytest.mark.asyncio
async def test_recheck_failure_keeps_pod():
    repo = _FakeRepo(["req-1"])
    pods = [_pod("orphan", "req-gone")]
    with _container(repo):
        strategy, core = _strategy(pods)

        def _break_storage() -> None:
            repo.fail = True

        core.on_list = _break_storage
        await _sweep(strategy)
    assert core.deleted == []


# ---------------------------------------------------------------------------
# Startup reconciler follows the same rules
# ---------------------------------------------------------------------------


async def _reconcile(strategy: K8sProviderStrategy):
    await strategy._reconciliation_lifecycle.run_startup_reconciler()  # type: ignore[attr-defined]
    return strategy.last_reconciliation_report


@pytest.mark.asyncio
async def test_reconciler_adopts_owned_and_reports_orphan():
    pods = [_pod("owned", "req-1"), _pod("orphan", "req-gone")]
    with _container(_FakeRepo(["req-1"])):
        strategy, core = _strategy(pods)
        report = await _reconcile(strategy)
    assert report is not None and report.completed
    assert report.pods_adopted == 1
    assert [o.pod_name for o in report.orphans] == ["orphan"]
    assert core.deleted == []


@pytest.mark.asyncio
async def test_reconciler_classifies_nothing_for_empty_store():
    pods = [_pod("p1", "req-1")]
    with _container(_FakeRepo([])):
        strategy, _ = _strategy(pods)
        report = await _reconcile(strategy)
    assert report is not None
    assert report.orphans == [] and report.pods_adopted == 0


@pytest.mark.asyncio
async def test_reconciler_classifies_nothing_when_store_unreadable():
    pods = [_pod("p1", "req-1")]
    with _container(_FakeRepo(["req-1"], fail=True)):
        strategy, _ = _strategy(pods)
        report = await _reconcile(strategy)
    assert report is not None
    assert report.orphans == [] and report.pods_adopted == 0
    assert report.completed is False and report.error


@pytest.mark.asyncio
async def test_reconciler_ignores_unlabeled_and_foreign_pods():
    pods = [
        _pod("no-rid", None),
        _pod("theirs", "req-gone", instance="orb-b"),
        _pod("pre-upgrade", "req-gone", instance=None),
    ]
    with _container(_FakeRepo(["req-1"])):
        strategy, _ = _strategy(pods)
        report = await _reconcile(strategy)
    assert report is not None
    assert report.orphans == [] and report.pods_adopted == 0


@pytest.mark.asyncio
async def test_reconciler_without_lookup_classifies_nothing():
    with patch("orb.infrastructure.di.container.get_container", side_effect=RuntimeError):
        strategy, _ = _strategy([_pod("p1", "req-1")])
    lifecycle = strategy._reconciliation_lifecycle  # type: ignore[attr-defined]
    _drop_lookup(lifecycle)
    report = await _reconcile(strategy)
    assert report is not None
    assert report.orphans == [] and report.pods_adopted == 0


# ---------------------------------------------------------------------------
# Created pods carry the instance label
# ---------------------------------------------------------------------------


def test_created_pods_and_workloads_carry_the_instance_label():
    from orb.providers.k8s.infrastructure.handlers.shared.label_stamper import (
        stamp_native_workload_body,
    )
    from orb.providers.k8s.utilities.pod_spec import build_pod_labels

    request = SimpleNamespace(request_id="req-1", template_id="tpl")

    labels = build_pod_labels(request, machine_id="m", instance_id=INSTANCE)  # type: ignore[arg-type]
    assert labels["orb.io/orb-instance"] == INSTANCE
    assert "orb.io/orb-instance" not in build_pod_labels(request, machine_id="m")  # type: ignore[arg-type]

    body = stamp_native_workload_body(
        {},
        workload_name="w",
        namespace="orb",
        replicas=1,
        request=request,
        label_prefix="orb.io",
        instance_id=INSTANCE,
    )
    assert body["metadata"]["labels"]["orb.io/orb-instance"] == INSTANCE
    assert body["spec"]["template"]["metadata"]["labels"]["orb.io/orb-instance"] == INSTANCE


def test_orb_instance_id_must_be_a_valid_label_value():
    from orb.providers.k8s.configuration.config import K8sProviderConfig

    assert K8sProviderConfig(in_cluster=True, orb_instance_id="  ").orb_instance_id is None  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        K8sProviderConfig(in_cluster=True, orb_instance_id="bad value!")  # type: ignore[call-arg]

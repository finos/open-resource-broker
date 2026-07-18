"""Gap-filling unit tests for StartupReconciler.

Targets uncovered lines in startup_reconciler.py:
- Line 173: _classify_pod returns None (no metadata / no pod_name) — run() path
- Lines 199-201: run() outer exception handler
- Lines 228-229, 235: run_async() known_request_ids failure path
- Lines 250-251: run_async() namespace raises exception (recorded as warning)
- Lines 279-281: run_async() outer exception handler
- Lines 313: _reconcile_namespace() _classify_pod returns None path
- Line 379: _classify_pod() metadata is None → returns None
- Line 382: _classify_pod() pod_name is None/empty → returns None
- Lines 487-499: _extract_status_reason() terminated/waiting/scheduled branches
- Line 505: _extract_status_reason() PodScheduled condition returns reason
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.reconciliation.startup_reconciler import (
    ReconciliationReport,
    StartupReconciler,
    _extract_status_reason,
    _is_pod_ready,
)
from orb.providers.k8s.watch.pod_state_cache import PodStateCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**kwargs: Any) -> K8sProviderConfig:
    defaults: dict[str, Any] = {"namespace": "default"}
    defaults.update(kwargs)
    return K8sProviderConfig(**defaults)  # type: ignore[call-arg]


def _make_reconciler(
    pods: list[Any] | None = None,
    known: list[str] | None = None,
    *,
    config: K8sProviderConfig | None = None,
    known_request_ids_raises: bool = False,
) -> tuple[StartupReconciler, PodStateCache, MagicMock]:
    cfg = config or _make_config()
    cache = PodStateCache()
    client = MagicMock()

    if known_request_ids_raises:

        def _known_ids_fn():
            raise RuntimeError("storage-unavailable")
    else:
        _known_ids = known or []

        def _known_ids_fn():
            return _known_ids

    if pods is not None:
        client.core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=pods)
        client.core_v1.list_pod_for_all_namespaces.return_value = SimpleNamespace(items=pods)

    reconciler = StartupReconciler(
        kubernetes_client=client,
        config=cfg,
        cache=cache,
        logger=MagicMock(),
        known_request_ids=_known_ids_fn,
    )
    return reconciler, cache, client


def _pod(
    name: str = "pod-1",
    request_id: str = "req-1",
    namespace: str = "default",
    phase: str = "Running",
    ready: bool = True,
) -> Any:
    labels = {
        "orb.io/managed": "true",
        "orb.io/request-id": request_id,
    }
    metadata = SimpleNamespace(
        name=name,
        namespace=namespace,
        labels=labels,
        creation_timestamp=None,
    )
    conditions = []
    if ready:
        conditions.append(SimpleNamespace(type="Ready", status="True"))
    status = SimpleNamespace(
        phase=phase,
        pod_ip="10.0.0.1",
        host_ip="192.168.1.1",
        start_time=None,
        conditions=conditions,
        container_statuses=[],
    )
    spec = SimpleNamespace(node_name="node-1")
    return SimpleNamespace(metadata=metadata, status=status, spec=spec)


def _pod_no_metadata() -> Any:
    return SimpleNamespace(metadata=None, status=None, spec=None)


def _pod_no_name() -> Any:
    metadata = SimpleNamespace(
        name=None,
        namespace="default",
        labels={"orb.io/managed": "true"},
        creation_timestamp=None,
    )
    return SimpleNamespace(metadata=metadata, status=None, spec=None)


# ---------------------------------------------------------------------------
# _classify_pod — metadata=None and pod_name=None paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_classify_pod_returns_none_when_metadata_is_none() -> None:
    """_classify_pod returns None for pods with no metadata (line 379)."""
    reconciler, _, _ = _make_reconciler(pods=[], known=["req-1"])
    result = reconciler._classify_pod(
        _pod_no_metadata(),
        namespace="default",
        known_ids={"req-1"},
        request_id_label="orb.io/request-id",
    )
    assert result is None


@pytest.mark.unit
def test_classify_pod_returns_none_when_pod_name_is_none() -> None:
    """_classify_pod returns None for pods with empty name (line 382)."""
    reconciler, _, _ = _make_reconciler(pods=[], known=["req-1"])
    result = reconciler._classify_pod(
        _pod_no_name(),
        namespace="default",
        known_ids={"req-1"},
        request_id_label="orb.io/request-id",
    )
    assert result is None


# ---------------------------------------------------------------------------
# run() — classify_pod returns None → continue (line 173)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_skips_pods_that_classify_as_none() -> None:
    """run() continues when _classify_pod returns None (no-metadata pod)."""
    reconciler, cache, client = _make_reconciler(
        pods=[_pod_no_metadata(), _pod(name="real-pod", request_id="req-1")],
        known=["req-1"],
    )
    report = reconciler.run()

    assert report.completed is True
    assert report.pods_seen == 2
    assert report.pods_adopted == 1


# ---------------------------------------------------------------------------
# run() — outer exception handler (lines 199-201)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_outer_exception_captured_on_report() -> None:
    """run() catches top-level exceptions and records them on the report."""
    reconciler, _, client = _make_reconciler(pods=[], known=[])

    # Make the inner known_ids call fail hard to trigger the outer except
    with patch.object(reconciler, "_resolve_namespaces", side_effect=RuntimeError("resolve-fail")):
        report = reconciler.run()

    assert report.completed is False
    assert "resolve-fail" in (report.error or "")


# ---------------------------------------------------------------------------
# run_async() — known_request_ids failure (lines 228-229, 235)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_async_known_ids_failure_treats_pods_as_orphans() -> None:
    """run_async() treats all pods as orphans when known_request_ids() raises."""
    reconciler, cache, _ = _make_reconciler(
        pods=[_pod(name="pod-x", request_id="req-x")],
        known_request_ids_raises=True,
    )
    report = asyncio.run(reconciler.run_async())

    assert report.completed is True
    # Pod should be classified as orphan because known_ids is empty
    assert report.orphan_count == 1
    reconciler._logger.warning.assert_called()


# ---------------------------------------------------------------------------
# run_async() — namespace raises (lines 250-251)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_async_namespace_exception_is_logged_and_skipped() -> None:
    """run_async() logs a warning when one namespace raises and continues."""
    cfg = _make_config(namespaces=["ns-good", "ns-bad"])
    reconciler, cache, client = _make_reconciler(known=["req-1"], config=cfg)

    def _list_ns(namespace: str, **kw: Any) -> Any:
        if namespace == "ns-bad":
            raise RuntimeError("ns-bad explodes")
        return SimpleNamespace(items=[_pod(name="pod-good", request_id="req-1")])

    client.core_v1.list_namespaced_pod.side_effect = _list_ns

    report = asyncio.run(reconciler.run_async())

    # Must still complete — failure is per-namespace
    assert report.completed is True
    assert report.pods_adopted == 1
    reconciler._logger.warning.assert_called()


# ---------------------------------------------------------------------------
# run_async() — outer exception (lines 279-281)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_async_outer_exception_captured() -> None:
    """run_async() captures top-level exceptions and records them on the report."""
    reconciler, _, _ = _make_reconciler(pods=[], known=[])

    with patch.object(
        reconciler, "_resolve_namespaces", side_effect=RuntimeError("resolve-async-fail")
    ):
        report = asyncio.run(reconciler.run_async())

    assert report.completed is False
    assert "resolve-async-fail" in (report.error or "")


# ---------------------------------------------------------------------------
# _reconcile_namespace() — _classify_pod returns None (line 313)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reconcile_namespace_skips_pods_that_classify_as_none() -> None:
    """_reconcile_namespace() skips pods where _classify_pod returns None."""
    reconciler, cache, client = _make_reconciler(
        pods=[_pod_no_metadata(), _pod(name="real-pod", request_id="req-1")],
        known=["req-1"],
    )
    # Run via run_async() so _reconcile_namespace() is exercised
    report = asyncio.run(reconciler.run_async())

    assert report.completed is True
    assert report.pods_adopted == 1


# ---------------------------------------------------------------------------
# _extract_status_reason — terminated reason
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_status_reason_terminated_reason_returned() -> None:
    """_extract_status_reason returns terminated.reason when present."""
    terminated = SimpleNamespace(reason="OOMKilled")
    state = SimpleNamespace(terminated=terminated, waiting=None)
    cs = SimpleNamespace(state=state)

    result = _extract_status_reason([cs], [])

    assert result == "OOMKilled"


@pytest.mark.unit
def test_extract_status_reason_waiting_reason_returned() -> None:
    """_extract_status_reason returns waiting.reason when terminated is absent."""
    waiting = SimpleNamespace(reason="CrashLoopBackOff")
    state = SimpleNamespace(terminated=None, waiting=waiting)
    cs = SimpleNamespace(state=state)

    result = _extract_status_reason([cs], [])

    assert result == "CrashLoopBackOff"


@pytest.mark.unit
def test_extract_status_reason_container_state_none_skipped() -> None:
    """_extract_status_reason skips container statuses with state=None."""
    cs = SimpleNamespace(state=None)
    cond = SimpleNamespace(type="PodScheduled", status="False", reason="Unschedulable")

    result = _extract_status_reason([cs], [cond])

    assert result == "Unschedulable"


@pytest.mark.unit
def test_extract_status_reason_pod_scheduled_false_reason() -> None:
    """_extract_status_reason returns PodScheduled reason when status=False."""
    cond = SimpleNamespace(type="PodScheduled", status="False", reason="InsufficientResources")

    result = _extract_status_reason([], [cond])

    assert result == "InsufficientResources"


@pytest.mark.unit
def test_extract_status_reason_pod_scheduled_true_ignored() -> None:
    """_extract_status_reason does NOT return reason when PodScheduled status=True."""
    cond = SimpleNamespace(type="PodScheduled", status="True", reason="SomeReason")

    result = _extract_status_reason([], [cond])

    assert result is None


@pytest.mark.unit
def test_extract_status_reason_no_data_returns_none() -> None:
    """_extract_status_reason returns None when no matching conditions."""
    result = _extract_status_reason([], [])
    assert result is None


@pytest.mark.unit
def test_extract_status_reason_terminated_reason_none_falls_through() -> None:
    """_extract_status_reason falls through when terminated.reason is None."""
    terminated = SimpleNamespace(reason=None)
    state = SimpleNamespace(terminated=terminated, waiting=None)
    cs = SimpleNamespace(state=state)

    result = _extract_status_reason([cs], [])

    assert result is None


@pytest.mark.unit
def test_extract_status_reason_waiting_reason_none_falls_through() -> None:
    """_extract_status_reason falls through when waiting.reason is None."""
    waiting = SimpleNamespace(reason=None)
    state = SimpleNamespace(terminated=None, waiting=waiting)
    cs = SimpleNamespace(state=state)

    result = _extract_status_reason([cs], [])

    assert result is None


# ---------------------------------------------------------------------------
# _is_pod_ready — branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_pod_ready_returns_true_when_ready_condition_present() -> None:
    cond = SimpleNamespace(type="Ready", status="True")
    assert _is_pod_ready([cond]) is True


@pytest.mark.unit
def test_is_pod_ready_returns_false_when_condition_not_true() -> None:
    cond = SimpleNamespace(type="Ready", status="False")
    assert _is_pod_ready([cond]) is False


@pytest.mark.unit
def test_is_pod_ready_returns_false_when_no_ready_condition() -> None:
    cond = SimpleNamespace(type="PodScheduled", status="True")
    assert _is_pod_ready([cond]) is False


@pytest.mark.unit
def test_is_pod_ready_returns_false_for_empty_conditions() -> None:
    assert _is_pod_ready([]) is False


# ---------------------------------------------------------------------------
# ReconciliationReport — properties
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reconciliation_report_orphan_count_property() -> None:
    """ReconciliationReport.orphan_count returns len(orphans)."""
    from orb.providers.k8s.reconciliation.startup_reconciler import OrphanPod

    report = ReconciliationReport(
        orphans=[
            OrphanPod(pod_name="p1", namespace="ns", request_id=None, creation_timestamp=None),
            OrphanPod(pod_name="p2", namespace="ns", request_id="old", creation_timestamp=None),
        ]
    )
    assert report.orphan_count == 2

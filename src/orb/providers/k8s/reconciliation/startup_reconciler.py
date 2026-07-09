"""Startup reconciler — warms the pod cache and surfaces orphans on process start.

Run once at provider :meth:`initialize`, before the watch fleet is
spawned.  Crash-safety: when ORB restarts after an unclean shutdown
there may be managed pods alive in the cluster that the in-memory
``PodStateCache`` has not yet seen.  Without reconciliation the first
``check_hosts_status`` calls fall back to the per-request list path,
which is correct but slow when there are thousands of pods spread
across many requests.

What the reconciler does:

1. Lists every pod carrying the ``orb.io/managed=true`` label (or the
   provider's configured ``label_prefix`` equivalent) across the
   configured namespaces.  Cluster-scoped mode uses
   ``list_pod_for_all_namespaces``.
2. Cross-references each pod's ``orb.io/request-id`` label against the
   set of request ids ORB knows about (supplied by the caller via the
   ``known_request_ids`` callable so that this module stays decoupled
   from the storage layer).
3. Populates the :class:`PodStateCache` for every known request so the
   first cache read returns a warm result rather than ``None``.
4. Builds a :class:`ReconciliationReport` describing how many pods were
   adopted, how many requests are now warm, and which pods were
   orphaned (label says managed but no matching request id in storage).

The reconciler does NOT delete orphans — that is the orphan-GC's job
and is governed by :attr:`K8sProviderConfig.auto_cleanup_orphans`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.di.injectable import injectable
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.utilities.labels import build_label_selector as _build_label_selector
from orb.providers.k8s.utilities.pod_state import pod_status_string as _canonical_pod_status_string
from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import V1Pod


# Hard upper bound on how long the reconciler will wait for the list
# call to return before giving up.  The plan budget is 30s for 1000
# pods; we use 60s as a defensive cap so a slow apiserver does not
# block ``initialize`` indefinitely.
_LIST_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class OrphanPod:
    """A pod that carries the managed label but has no matching request id."""

    pod_name: str
    namespace: str
    request_id: Optional[str]
    creation_timestamp: Optional[str]


@dataclass
class ReconciliationReport:
    """Outcome of one :meth:`StartupReconciler.run` call.

    Used by the strategy to log a summary line and by the orphan-GC
    when it is invoked for the first time (so the GC can immediately
    act on the pods the reconciler classified as orphans without
    re-listing).
    """

    namespaces: list[str] = field(default_factory=list)
    pods_seen: int = 0
    pods_adopted: int = 0
    requests_warmed: int = 0
    orphans: list[OrphanPod] = field(default_factory=list)
    duration_seconds: float = 0.0
    completed: bool = False
    error: Optional[str] = None

    @property
    def orphan_count(self) -> int:
        return len(self.orphans)


@injectable
class StartupReconciler:
    """Warm the :class:`PodStateCache` and identify orphans at provider start.

    Args:
        kubernetes_client: Provider API facade.  Uses ``core_v1``.
        config: Validated :class:`K8sProviderConfig`.
        cache: The :class:`PodStateCache` to seed with adopted pods.
        logger: Logging port.
        known_request_ids: Callable returning the set of request ids ORB
            currently has in storage.  Called once per :meth:`run`.
            Decouples this module from the storage repository (which
            lives in ``infrastructure/storage/``) — callers supply a
            closure over their own ``request_repository``.

    The class is constructor-injected (no late binding) so the wiring
    in :class:`K8sProviderStrategy` is straightforward.
    """

    def __init__(
        self,
        kubernetes_client: K8sClient,
        config: K8sProviderConfig,
        cache: PodStateCache,
        logger: LoggingPort,
        *,
        known_request_ids: Callable[[], Iterable[str]],
    ) -> None:
        self._client = kubernetes_client
        self._config = config
        self._cache = cache
        self._logger = logger
        self._known_request_ids_fn = known_request_ids

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> ReconciliationReport:
        """List managed pods, populate the cache, classify orphans.

        Returns a populated :class:`ReconciliationReport`.  Errors are
        captured on the report rather than raised so a transient list
        failure does not crash provider initialisation — the watcher
        will reconcile naturally once it starts.
        """
        report = ReconciliationReport()
        start = time.monotonic()

        try:
            namespaces = self._resolve_namespaces()
            report.namespaces = [ns if ns is not None else "*" for ns in namespaces]

            try:
                known_ids = {str(rid) for rid in self._known_request_ids_fn()}
            except Exception as exc:
                # The caller's storage lookup is not allowed to break
                # the provider start sequence.  We log and proceed with
                # an empty known set, which classifies every pod as an
                # orphan — that is the safest signal for the operator.
                self._logger.warning(
                    "StartupReconciler: known_request_ids lookup failed; "
                    "treating every managed pod as an orphan: %s",
                    exc,
                    exc_info=True,
                )
                known_ids = set()

            label_selector = _build_label_selector(self._config.label_prefix, "managed", "true")
            request_id_label = f"{self._config.label_prefix}/request-id"

            for ns in namespaces:
                pods = self._list_pods(ns, label_selector)
                report.pods_seen += len(pods)
                for pod in pods:
                    classified = self._classify_pod(
                        pod,
                        namespace=ns,
                        known_ids=known_ids,
                        request_id_label=request_id_label,
                    )
                    if classified is None:
                        continue
                    if classified.kind == "adopted":
                        assert classified.state is not None  # narrow for pyright
                        self._cache.upsert(classified.state)
                        report.pods_adopted += 1
                    else:
                        report.orphans.append(classified.orphan)  # type: ignore[arg-type]

            # ``requests_warmed`` counts distinct request ids that the
            # reconciler populated into the cache.
            seen_requests: set[str] = set()
            for state in self._cache.all_states():
                if state.request_id in known_ids:
                    seen_requests.add(state.request_id)
            report.requests_warmed = len(seen_requests)

            report.completed = True
            self._logger.info(
                "Kubernetes reconciler completed: namespaces=%s pods_seen=%d "
                "pods_adopted=%d requests_warmed=%d orphans=%d",
                report.namespaces,
                report.pods_seen,
                report.pods_adopted,
                report.requests_warmed,
                report.orphan_count,
            )
        except Exception as exc:
            report.error = str(exc)
            self._logger.warning(
                "Kubernetes reconciler failed: %s (provider will start anyway)",
                exc,
                exc_info=True,
            )
        finally:
            report.duration_seconds = time.monotonic() - start
        return report

    async def run_async(self) -> ReconciliationReport:
        """Async variant of :meth:`run` that fans out namespace listing in parallel.

        Uses :func:`asyncio.gather` with ``return_exceptions=True`` so a
        failing namespace is logged and skipped without aborting the whole
        reconciliation.  The cache is mutated from the coroutine body — all
        upserts happen on the event-loop thread so no additional locking is
        required.
        """
        report = ReconciliationReport()
        start = time.monotonic()

        try:
            namespaces = self._resolve_namespaces()
            report.namespaces = [ns if ns is not None else "*" for ns in namespaces]

            try:
                known_ids = {str(rid) for rid in self._known_request_ids_fn()}
            except Exception as exc:
                self._logger.warning(
                    "StartupReconciler: known_request_ids lookup failed; "
                    "treating every managed pod as an orphan: %s",
                    exc,
                    exc_info=True,
                )
                known_ids = set()

            label_selector = _build_label_selector(self._config.label_prefix, "managed", "true")
            request_id_label = f"{self._config.label_prefix}/request-id"

            results = await asyncio.gather(
                *[
                    self._reconcile_namespace(ns, label_selector, request_id_label, known_ids)
                    for ns in namespaces
                ],
                return_exceptions=True,
            )

            for ns, result in zip(namespaces, results, strict=True):
                if isinstance(result, BaseException):
                    ns_label = ns if ns is not None else "*"
                    self._logger.warning(
                        "StartupReconciler: namespace=%s raised an exception (skipping): %s",
                        ns_label,
                        result,
                        exc_info=result,
                    )
                else:
                    pods_seen, adopted, orphans = result
                    report.pods_seen += pods_seen
                    report.pods_adopted += adopted
                    report.orphans.extend(orphans)

            seen_requests: set[str] = set()
            for state in self._cache.all_states():
                if state.request_id in known_ids:
                    seen_requests.add(state.request_id)
            report.requests_warmed = len(seen_requests)

            report.completed = True
            self._logger.info(
                "Kubernetes reconciler completed: namespaces=%s pods_seen=%d "
                "pods_adopted=%d requests_warmed=%d orphans=%d",
                report.namespaces,
                report.pods_seen,
                report.pods_adopted,
                report.requests_warmed,
                report.orphan_count,
            )
        except Exception as exc:
            report.error = str(exc)
            self._logger.warning(
                "Kubernetes reconciler failed: %s (provider will start anyway)",
                exc,
                exc_info=True,
            )
        finally:
            report.duration_seconds = time.monotonic() - start
        return report

    async def _reconcile_namespace(
        self,
        namespace: Optional[str],
        label_selector: str,
        request_id_label: str,
        known_ids: set[str],
    ) -> tuple[int, int, list[OrphanPod]]:
        """List and classify pods for one namespace; runs the blocking list in a thread.

        Returns ``(pods_seen, pods_adopted, orphans)`` so the caller can
        aggregate stats without locking.
        """
        pods = await asyncio.to_thread(self._list_pods, namespace, label_selector)
        adopted = 0
        orphans: list[OrphanPod] = []
        for pod in pods:
            classified = self._classify_pod(
                pod,
                namespace=namespace,
                known_ids=known_ids,
                request_id_label=request_id_label,
            )
            if classified is None:
                continue
            if classified.kind == "adopted":
                assert classified.state is not None  # narrow for pyright
                self._cache.upsert(classified.state)
                adopted += 1
            else:
                orphans.append(classified.orphan)  # type: ignore[arg-type]
        return len(pods), adopted, orphans

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_namespaces(self) -> list[Optional[str]]:
        """Translate ``config.namespaces`` into a list of namespaces to list.

        Mirrors :meth:`MultiNamespaceWatcher._resolve_watched_namespaces`
        so the reconciler and watcher agree on the same scope.
        """
        explicit = self._config.namespaces
        if explicit is None:
            return [self._config.namespace]
        if explicit == ["*"]:
            return [None]
        return list(explicit)

    def _list_pods(self, namespace: Optional[str], label_selector: str) -> list[Any]:
        """List pods for ``namespace`` (None = cluster-wide).

        Uses ``timeout_seconds`` so a stalled apiserver cannot block
        provider start indefinitely.
        """
        core_v1 = self._client.core_v1
        try:
            if namespace is None:
                response = core_v1.list_pod_for_all_namespaces(
                    label_selector=label_selector,
                    timeout_seconds=_LIST_TIMEOUT_SECONDS,
                )
            else:
                response = core_v1.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=label_selector,
                    timeout_seconds=_LIST_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            self._logger.warning(
                "StartupReconciler: list pods failed for namespace=%s: %s",
                namespace if namespace is not None else "*",
                exc,
                exc_info=True,
            )
            return []
        return list(getattr(response, "items", []) or [])

    def _classify_pod(
        self,
        pod: V1Pod,
        *,
        namespace: Optional[str],
        known_ids: set[str],
        request_id_label: str,
    ) -> Optional[_Classified]:
        """Return whether ``pod`` is adopted into the cache or an orphan."""
        metadata = getattr(pod, "metadata", None)
        if metadata is None:
            return None
        pod_name = getattr(metadata, "name", None)
        if not pod_name:
            return None
        labels = dict(getattr(metadata, "labels", None) or {})
        request_id = labels.get(request_id_label)
        pod_namespace = getattr(metadata, "namespace", None) or namespace or ""
        creation_timestamp = getattr(metadata, "creation_timestamp", None)

        if request_id is None or request_id not in known_ids:
            return _Classified(
                kind="orphan",
                orphan=OrphanPod(
                    pod_name=pod_name,
                    namespace=str(pod_namespace),
                    request_id=request_id,
                    creation_timestamp=(
                        str(creation_timestamp) if creation_timestamp is not None else None
                    ),
                ),
            )

        state = _pod_to_state(pod, request_id=request_id, namespace=str(pod_namespace))
        return _Classified(kind="adopted", state=state)


@dataclass
class _Classified:
    """Internal sum type returned by :meth:`StartupReconciler._classify_pod`."""

    kind: str  # "adopted" | "orphan"
    state: Optional[PodState] = None
    orphan: Optional[OrphanPod] = None


# ---------------------------------------------------------------------------
# Pod -> PodState translator
# ---------------------------------------------------------------------------
#
# Mirrors :meth:`K8sWatcher._pod_to_state` exactly so the
# reconciler and the watcher produce identical cache entries.  Lives at
# module scope so unit tests can exercise the translator without
# constructing the full reconciler.


def _pod_to_state(pod: V1Pod, *, request_id: str, namespace: str) -> PodState:
    metadata = getattr(pod, "metadata", None)
    status = getattr(pod, "status", None)
    spec = getattr(pod, "spec", None)

    name = getattr(metadata, "name", "") if metadata is not None else ""
    labels = dict(getattr(metadata, "labels", None) or {}) if metadata is not None else {}
    phase = getattr(status, "phase", None) if status is not None else None
    pod_ip = getattr(status, "pod_ip", None) if status is not None else None
    host_ip = getattr(status, "host_ip", None) if status is not None else None
    node_name = getattr(spec, "node_name", None) if spec is not None else None
    start_time = getattr(status, "start_time", None) if status is not None else None
    conditions = list(getattr(status, "conditions", None) or []) if status is not None else []
    container_statuses = (
        list(getattr(status, "container_statuses", None) or []) if status is not None else []
    )

    # Read provider-API type from pod label for context-aware Succeeded mapping.
    label_prefix = "orb.io"
    pod_provider_api: Optional[str] = labels.get(f"{label_prefix}/provider-api")

    ready = _is_pod_ready(conditions)
    status_str = _pod_status_string(phase, ready, provider_api=pod_provider_api)
    reason = _extract_status_reason(container_statuses, conditions)

    return PodState(
        request_id=request_id,
        pod_name=name,
        namespace=namespace,
        status=status_str,
        phase=phase,
        ready=ready,
        pod_ip=pod_ip,
        host_ip=host_ip,
        node_name=node_name,
        status_reason=reason,
        start_time=str(start_time) if start_time is not None else None,
        labels=labels,
        deleted=False,
    )


def _is_pod_ready(conditions: list[Any]) -> bool:
    for cond in conditions:
        ctype = getattr(cond, "type", None)
        cstatus = getattr(cond, "status", None)
        if ctype == "Ready" and cstatus == "True":
            return True
    return False


def _pod_status_string(
    phase: Optional[str], ready: bool, *, provider_api: Optional[str] = None
) -> str:
    """Delegate to the canonical :func:`pod_state.pod_status_string`."""
    return _canonical_pod_status_string(phase, ready, provider_api=provider_api)


def _extract_status_reason(
    container_statuses: list[Any],
    conditions: list[Any],
) -> Optional[str]:
    for cs in container_statuses:
        state = getattr(cs, "state", None)
        if state is None:
            continue
        terminated = getattr(state, "terminated", None)
        if terminated is not None:
            reason = getattr(terminated, "reason", None)
            if reason:
                return str(reason)
        waiting = getattr(state, "waiting", None)
        if waiting is not None:
            reason = getattr(waiting, "reason", None)
            if reason:
                return str(reason)
    for cond in conditions:
        ctype = getattr(cond, "type", None)
        cstatus = getattr(cond, "status", None)
        reason = getattr(cond, "reason", None)
        if ctype == "PodScheduled" and cstatus == "False" and reason:
            return str(reason)
    return None


__all__ = [
    "OrphanPod",
    "ReconciliationReport",
    "StartupReconciler",
]

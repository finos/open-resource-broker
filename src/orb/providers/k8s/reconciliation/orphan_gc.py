"""Periodic orphan garbage collector for the Kubernetes provider.

Long-running asyncio task that walks the cluster every
``orphan_gc_interval_seconds`` and identifies pods that:

* carry the ``orb.io/managed=true`` label (so ORB has stamped them
  during a prior acquisition);
* have a ``orb.io/request-id`` label whose value is not in ORB
  storage.

Such pods are "orphans": the controller plane has lost track of them.
Behaviour is governed by :attr:`K8sProviderConfig.auto_cleanup_orphans`:

* ``False`` (default) — the GC logs a structured warning per orphan
  and increments an internal counter.  The operator can inspect the
  pod and either delete it manually or flip the flag.
* ``True``             — the GC deletes the orphan pod (best-effort —
  404s are swallowed).

The task is opt-in via :attr:`K8sProviderConfig.orphan_gc_enabled`
(default ``False``) so a new deployment never starts background
deletion without operator consent.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.reconciliation.startup_reconciler import OrphanPod

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import V1Pod


# Hard upper bound on the list call.  Matches the startup reconciler so
# the two paths agree on what "stalled apiserver" means.
_LIST_TIMEOUT_SECONDS = 60


@dataclass
class OrphanGCStats:
    """Cumulative orphan-GC statistics surfaced for metrics / debug."""

    runs: int = 0
    last_run_at: float = 0.0
    last_orphans_found: int = 0
    total_orphans_found: int = 0
    total_orphans_deleted: int = 0
    delete_failures: int = 0
    last_error: Optional[str] = None
    namespaces_checked: list[str] = field(default_factory=list)


@injectable
class OrphanGarbageCollector:
    """Periodic orphan detector and (optional) deleter.

    Args:
        kubernetes_client: Provider API facade.
        config: Validated :class:`K8sProviderConfig`.
        logger: Logging port.
        known_request_ids: Callable returning the request ids ORB knows
            about, evaluated fresh on every run so the GC always sees
            the latest storage view (decoupled from the storage layer
            same way the startup reconciler is).
        interval_seconds: Override for
            :attr:`K8sProviderConfig.orphan_gc_interval_seconds`
            (tests inject a much shorter interval).
    """

    def __init__(
        self,
        kubernetes_client: K8sClient,
        config: K8sProviderConfig,
        logger: LoggingPort,
        *,
        known_request_ids: Callable[[], Iterable[str]],
        interval_seconds: Optional[float] = None,
    ) -> None:
        self._client = kubernetes_client
        self._config = config
        self._logger = logger
        self._known_request_ids_fn = known_request_ids
        self._interval = float(
            interval_seconds if interval_seconds is not None else config.orphan_gc_interval_seconds
        )

        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self.stats = OrphanGCStats()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Spawn the GC task on the current event loop.

        Idempotent.  The caller is responsible for gating on
        :attr:`K8sProviderConfig.orphan_gc_enabled`; this method
        does not consult the flag so tests can drive the loop directly.
        """
        if self.is_running():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="k8s-orphan-gc")

    async def stop(self) -> None:
        """Stop the loop and wait for the task to settle."""
        self._stop_event.set()
        task = self._task
        if task is None:
            return
        if not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # pragma: no cover
                    pass
        self._task = None

    # ------------------------------------------------------------------
    # Single sweep — used by the loop and exposed for tests / cron
    # ------------------------------------------------------------------

    async def run_once(self) -> list[OrphanPod]:
        """Perform exactly one sweep; return the orphans found."""
        return await asyncio.to_thread(self._run_once_sync)

    def _run_once_sync(self) -> list[OrphanPod]:
        """Synchronous body of one sweep — runs on the worker thread."""
        self.stats.runs += 1
        self.stats.last_run_at = time.monotonic()
        self.stats.last_orphans_found = 0
        self.stats.last_error = None

        try:
            known_ids = {str(rid) for rid in self._known_request_ids_fn()}
        except Exception as exc:  # noqa: BLE001 — defensive
            self.stats.last_error = f"known_request_ids lookup failed: {exc}"
            self._logger.warning(
                "Orphan GC: known_request_ids lookup failed; skipping sweep: %s",
                exc,
                exc_info=True,
            )
            return []

        namespaces = self._resolve_namespaces()
        self.stats.namespaces_checked = [ns if ns is not None else "*" for ns in namespaces]
        label_selector = f"{self._config.label_prefix}/managed=true"
        request_id_label = f"{self._config.label_prefix}/request-id"

        orphans: list[OrphanPod] = []
        for ns in namespaces:
            pods = self._list_pods(ns, label_selector)
            for pod in pods:
                orphan = self._classify_orphan(
                    pod,
                    namespace=ns,
                    known_ids=known_ids,
                    request_id_label=request_id_label,
                )
                if orphan is not None:
                    orphans.append(orphan)

        self.stats.last_orphans_found = len(orphans)
        self.stats.total_orphans_found += len(orphans)

        for orphan in orphans:
            if self._config.auto_cleanup_orphans:
                self._delete_orphan(orphan)
            else:
                self._logger.warning(
                    "Orphan pod detected (auto_cleanup_orphans=False): "
                    "pod=%s namespace=%s request_id=%s created=%s",
                    orphan.pod_name,
                    orphan.namespace,
                    orphan.request_id,
                    orphan.creation_timestamp,
                )

        return orphans

    # ------------------------------------------------------------------
    # Loop body
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Run :meth:`run_once` on the configured interval until stop()."""
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:  # pragma: no cover — propagated by stop()
                raise
            except Exception as exc:  # noqa: BLE001 — defensive
                self.stats.last_error = str(exc)
                self._logger.warning("Orphan GC sweep raised: %s (continuing)", exc, exc_info=True)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
                return
            except asyncio.TimeoutError:
                continue

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_namespaces(self) -> list[Optional[str]]:
        explicit = self._config.namespaces
        if explicit is None:
            return [self._config.namespace]
        if explicit == ["*"]:
            return [None]
        return list(explicit)

    def _list_pods(self, namespace: Optional[str], label_selector: str) -> list[Any]:
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
        except Exception as exc:  # noqa: BLE001 — defensive
            self.stats.last_error = f"list pods failed: {exc}"
            self._logger.warning(
                "Orphan GC: list pods failed for namespace=%s: %s",
                namespace if namespace is not None else "*",
                exc,
                exc_info=True,
            )
            return []
        return list(getattr(response, "items", []) or [])

    @staticmethod
    def _classify_orphan(
        pod: "V1Pod",
        *,
        namespace: Optional[str],
        known_ids: set[str],
        request_id_label: str,
    ) -> Optional[OrphanPod]:
        metadata = getattr(pod, "metadata", None)
        if metadata is None:
            return None
        pod_name = getattr(metadata, "name", None)
        if not pod_name:
            return None
        labels = dict(getattr(metadata, "labels", None) or {})
        request_id = labels.get(request_id_label)
        if request_id is not None and request_id in known_ids:
            return None
        pod_namespace = getattr(metadata, "namespace", None) or namespace or ""
        creation_timestamp = getattr(metadata, "creation_timestamp", None)
        return OrphanPod(
            pod_name=pod_name,
            namespace=str(pod_namespace),
            request_id=request_id,
            creation_timestamp=(
                str(creation_timestamp) if creation_timestamp is not None else None
            ),
        )

    def _delete_orphan(self, orphan: OrphanPod) -> None:
        """Best-effort delete; swallow 404 (already gone)."""
        try:
            self._client.core_v1.delete_namespaced_pod(
                name=orphan.pod_name,
                namespace=orphan.namespace,
            )
            self.stats.total_orphans_deleted += 1
            self._logger.info(
                "Orphan pod deleted: pod=%s namespace=%s request_id=%s",
                orphan.pod_name,
                orphan.namespace,
                orphan.request_id,
            )
        except Exception as exc:  # noqa: BLE001 — defensive
            if _is_not_found(exc):
                # Already gone — treat as success for accounting.
                self.stats.total_orphans_deleted += 1
                self._logger.debug("Orphan pod %s already gone (404)", orphan.pod_name)
                return
            self.stats.delete_failures += 1
            self.stats.last_error = f"delete pod {orphan.pod_name}: {exc}"
            self._logger.warning(
                "Orphan pod delete failed: pod=%s namespace=%s error=%s",
                orphan.pod_name,
                orphan.namespace,
                exc,
            )


def _is_not_found(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` is a 404 ``ApiException``."""
    try:
        from kubernetes.client.exceptions import ApiException  # noqa: PLC0415
    except ImportError:  # pragma: no cover — extra not installed
        return False
    if not isinstance(exc, ApiException):
        return False
    return getattr(exc, "status", None) == 404


__all__ = ["OrphanGCStats", "OrphanGarbageCollector"]

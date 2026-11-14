"""Periodic orphan garbage collector for the Kubernetes provider.

Long-running asyncio task that walks the cluster every
``orphan_gc_interval_seconds`` and identifies pods that:

* carry the ``orb.io/managed=true`` label and this installation's
  ``orb.io/orb-instance`` label (so this ORB stamped them during a prior
  acquisition and no other installation sharing the cluster owns them);
* have a non-empty ``orb.io/request-id`` label whose value is not in ORB
  storage.

Such pods are "orphans": the controller plane has lost track of them.

The same sweep covers the custom resource kinds that plugin handlers
declare through ``register_k8s_handler(custom_resources=...)``: an object
of such a kind is an orphan under exactly the rules above, and is deleted
under the same guards.  Objects already being deleted are skipped.

The collector fails safe.  It does nothing when the instance id is not
configured, when the known-request-id lookup is missing, fails or returns
an empty set, and it re-checks storage for the pod's request immediately
before every delete.  Pods without a request-id label are reported but
never deleted.
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
import datetime
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.di.injectable import injectable
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.handler_plugins import K8sCustomResource, registered_custom_resources
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.reconciliation.startup_reconciler import OrphanPod
from orb.providers.k8s.utilities.labels import (
    build_owned_pods_selector as _build_owned_pods_selector,
    instance_label_key as _instance_label_key,
)

if TYPE_CHECKING:
    from kubernetes.client import V1Pod


# Hard upper bound on the list call.  Matches the startup reconciler so
# the two paths agree on what "stalled apiserver" means.
_LIST_TIMEOUT_SECONDS = 60

# Maximum number of concurrent delete calls issued in a single sweep.
# Avoids flooding the apiserver when a large batch of orphans is found.
_DELETE_CONCURRENCY = 50


@dataclass(frozen=True)
class OrphanCustomResource:
    """A custom resource object that carries the managed labels of an unknown request."""

    resource: K8sCustomResource
    name: str
    namespace: str
    request_id: Optional[str]
    creation_timestamp: Optional[str]


Orphan = OrphanPod | OrphanCustomResource


@dataclass
class OrphanGCStats:
    """Cumulative orphan-GC statistics surfaced for metrics / debug."""

    runs: int = 0
    last_run_at: float = 0.0
    last_orphans_found: int = 0
    total_orphans_found: int = 0
    total_orphans_deleted: int = 0
    delete_failures: int = 0
    skipped_unlabeled: int = 0
    skipped_recheck: int = 0
    last_error: Optional[str] = None
    namespaces_checked: list[str] = field(default_factory=list)
    custom_resources_checked: list[str] = field(default_factory=list)


@injectable
class OrphanGarbageCollector:
    """Periodic orphan detector and (optional) deleter.

    Args:
        kubernetes_client: Provider API facade.
        config: Validated :class:`K8sProviderConfig`.
        logger: Logging port.
        known_request_ids: Callable returning every request id ORB knows
            about (any status), evaluated fresh on every run so the GC
            always sees the latest storage view.  Must raise when storage
            cannot be read.  ``None`` disables the GC.
        existing_request_ids: Callable taking candidate request ids and
            returning those that exist in storage right now.  Consulted
            immediately before every delete.  Falls back to
            ``known_request_ids`` when omitted.
        interval_seconds: Override for
            :attr:`K8sProviderConfig.orphan_gc_interval_seconds`
            (tests inject a much shorter interval).
        custom_resources: Callable returning the custom resource kinds to
            sweep alongside pods, evaluated on every run.  Defaults to the
            kinds plugin handlers declared with ``register_k8s_handler``.
    """

    def __init__(
        self,
        kubernetes_client: K8sClient,
        config: K8sProviderConfig,
        logger: LoggingPort,
        *,
        known_request_ids: Optional[Callable[[], Iterable[str]]],
        existing_request_ids: Optional[Callable[[Iterable[str]], Iterable[str]]] = None,
        interval_seconds: Optional[float] = None,
        custom_resources: Optional[Callable[[], Iterable[K8sCustomResource]]] = None,
    ) -> None:
        self._client = kubernetes_client
        self._custom_resources_fn = custom_resources or registered_custom_resources
        self._config = config
        self._logger = logger
        self._known_request_ids_fn = known_request_ids
        self._existing_request_ids_fn = existing_request_ids
        self._warned: set[str] = set()
        self._interval = float(
            interval_seconds if interval_seconds is not None else config.orphan_gc_interval_seconds
        )

        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self.stats = OrphanGCStats()
        self._stats_lock = threading.Lock()

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
                except (asyncio.CancelledError, Exception) as exc:  # pragma: no cover
                    self._logger.debug("Orphan GC task raised during cancellation: %s", exc)
        self._task = None

    # ------------------------------------------------------------------
    # Single sweep — used by the loop and exposed for tests / cron
    # ------------------------------------------------------------------

    async def run_once(self) -> list[Orphan]:
        """Perform exactly one sweep across all namespaces in parallel.

        Namespace fan-out runs concurrently via :func:`asyncio.gather` so
        a slow apiserver response for one namespace does not block the
        others.  Exceptions from individual namespaces are logged and
        skipped; the overall sweep continues.  Each declared custom
        resource kind is listed in every namespace the same way.
        """
        self.stats.runs += 1
        self.stats.last_run_at = time.monotonic()
        self.stats.last_orphans_found = 0
        self.stats.last_error = None

        instance_id = self._config.orb_instance_id
        if not instance_id:
            self._skip(
                "no_instance_id",
                "orb_instance_id is not configured, so ownership of managed pods "
                "cannot be established",
            )
            return []
        if self._known_request_ids_fn is None:
            self._skip("no_known_ids_fn", "no request-id lookup is available")
            return []
        try:
            known_ids = await asyncio.to_thread(self._read_known_ids)
        except Exception as exc:
            self.stats.last_error = f"known_request_ids lookup failed: {exc}"
            self._logger.warning(
                "Orphan GC: known_request_ids lookup failed; skipping sweep: %s",
                exc,
                exc_info=True,
            )
            return []
        if not known_ids:
            self._skip("empty_known_ids", "the request store returned no request ids")
            return []

        namespaces = self._resolve_namespaces()
        self.stats.namespaces_checked = [ns if ns is not None else "*" for ns in namespaces]
        label_selector = _build_owned_pods_selector(self._config.label_prefix, instance_id)
        request_id_label = f"{self._config.label_prefix}/request-id"

        resources = self._read_custom_resources()
        self.stats.custom_resources_checked = [str(resource) for resource in resources]

        targets: list[tuple[Optional[K8sCustomResource], Optional[str]]] = [
            (None, ns) for ns in namespaces
        ]
        targets.extend((resource, ns) for resource in resources for ns in namespaces)
        results = await asyncio.gather(
            *[
                self._gc_namespace(ns, label_selector, request_id_label, known_ids)
                if resource is None
                else self._gc_custom_namespace(
                    resource, ns, label_selector, request_id_label, known_ids
                )
                for resource, ns in targets
            ],
            return_exceptions=True,
        )

        orphans: list[Orphan] = []
        for (resource, ns), result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                ns_label = ns if ns is not None else "*"
                kind = "pods" if resource is None else str(resource)
                self.stats.last_error = f"gc_namespace failed for {ns_label} ({kind}): {result}"
                self._logger.warning(
                    "Orphan GC: namespace=%s kind=%s raised an exception (continuing): %s",
                    ns_label,
                    kind,
                    result,
                    exc_info=result,
                )
            else:
                orphans.extend(result)

        self.stats.last_orphans_found = len(orphans)
        self.stats.total_orphans_found += len(orphans)

        if self._config.auto_cleanup_orphans:
            await self._delete_orphans_concurrently(orphans)
        else:
            for orphan in orphans:
                if isinstance(orphan, OrphanCustomResource):
                    self._logger.warning(
                        "Orphan custom resource detected (auto_cleanup_orphans=False): "
                        "kind=%s name=%s namespace=%s request_id=%s created=%s",
                        orphan.resource,
                        orphan.name,
                        orphan.namespace,
                        orphan.request_id,
                        orphan.creation_timestamp,
                    )
                    continue
                self._logger.warning(
                    "Orphan pod detected (auto_cleanup_orphans=False): "
                    "pod=%s namespace=%s request_id=%s created=%s",
                    orphan.pod_name,
                    orphan.namespace,
                    orphan.request_id,
                    orphan.creation_timestamp,
                )

        return orphans

    def _read_custom_resources(self) -> list[K8sCustomResource]:
        """Return the declared custom resource kinds; a failing lookup sweeps pods only."""
        try:
            return list(dict.fromkeys(self._custom_resources_fn()))
        except Exception as exc:
            self._logger.warning(
                "Orphan GC: custom resource lookup failed; sweeping pods only: %s", exc
            )
            return []

    def _read_known_ids(self) -> set[str]:
        assert self._known_request_ids_fn is not None  # noqa: S101  # narrowed by caller
        return {str(rid) for rid in self._known_request_ids_fn()}

    def _skip(self, reason: str, detail: str) -> None:
        """Record a refused sweep; warn the first time per reason, debug afterwards."""
        self.stats.last_error = f"sweep skipped: {detail}"
        log = self._logger.debug if reason in self._warned else self._logger.warning
        self._warned.add(reason)
        log("Orphan GC: skipping sweep and deleting nothing: %s", detail)

    def _request_still_unknown(self, request_id: str) -> bool:
        """Return True only when storage confirms *request_id* does not exist.

        Any lookup failure counts as "unknown state" and returns False so the
        pod is kept.
        """
        try:
            if self._existing_request_ids_fn is not None:
                existing = {str(rid) for rid in self._existing_request_ids_fn([request_id])}
            elif self._known_request_ids_fn is not None:
                existing = {str(rid) for rid in self._known_request_ids_fn()}
            else:
                return False
        except Exception as exc:
            self._logger.warning(
                "Orphan GC: re-check of request %s failed; keeping pod: %s", request_id, exc
            )
            return False
        return request_id not in existing

    async def _gc_namespace(
        self,
        namespace: Optional[str],
        label_selector: str,
        request_id_label: str,
        known_ids: set[str],
    ) -> list[OrphanPod]:
        """Collect orphans for a single namespace; runs the blocking list call in a thread."""
        pods = await asyncio.to_thread(self._list_pods, namespace, label_selector)
        orphans: list[OrphanPod] = []
        for pod in pods:
            orphan = self._classify_orphan(
                pod,
                namespace=namespace,
                known_ids=known_ids,
                request_id_label=request_id_label,
                instance_label=_instance_label_key(self._config.label_prefix),
                instance_id=self._config.orb_instance_id or "",
            )
            if orphan is not None and not orphan.request_id:
                self.stats.skipped_unlabeled += 1
                self._logger.warning(
                    "Managed pod has no request-id label; not deleting: pod=%s namespace=%s",
                    orphan.pod_name,
                    orphan.namespace,
                )
                continue
            if orphan is not None:
                orphans.append(orphan)
        return orphans

    async def _gc_custom_namespace(
        self,
        resource: K8sCustomResource,
        namespace: Optional[str],
        label_selector: str,
        request_id_label: str,
        known_ids: set[str],
    ) -> list[OrphanCustomResource]:
        """Collect orphaned objects of one custom resource kind in one namespace."""
        objects = await asyncio.to_thread(
            self._list_custom_objects, resource, namespace, label_selector
        )
        orphans: list[OrphanCustomResource] = []
        for obj in objects:
            orphan = self._classify_custom_orphan(
                obj,
                resource=resource,
                namespace=namespace,
                known_ids=known_ids,
                request_id_label=request_id_label,
                instance_label=_instance_label_key(self._config.label_prefix),
                instance_id=self._config.orb_instance_id or "",
            )
            if orphan is not None and not orphan.request_id:
                self.stats.skipped_unlabeled += 1
                self._logger.warning(
                    "Managed custom resource has no request-id label; not deleting: "
                    "kind=%s name=%s namespace=%s",
                    resource,
                    orphan.name,
                    orphan.namespace,
                )
                continue
            if orphan is not None:
                orphans.append(orphan)
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
            except Exception as exc:
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
        except Exception as exc:
            self.stats.last_error = f"list pods failed: {exc}"
            self._logger.warning(
                "Orphan GC: list pods failed for namespace=%s: %s",
                namespace if namespace is not None else "*",
                exc,
                exc_info=True,
            )
            return []
        return list(getattr(response, "items", []) or [])

    def _list_custom_objects(
        self,
        resource: K8sCustomResource,
        namespace: Optional[str],
        label_selector: str,
    ) -> list[dict[str, Any]]:
        api = self._client.custom_objects_api
        try:
            if namespace is None:
                response = api.list_cluster_custom_object(
                    group=resource.group,
                    version=resource.version,
                    plural=resource.plural,
                    label_selector=label_selector,
                    timeout_seconds=_LIST_TIMEOUT_SECONDS,
                )
            else:
                response = api.list_namespaced_custom_object(
                    group=resource.group,
                    version=resource.version,
                    namespace=namespace,
                    plural=resource.plural,
                    label_selector=label_selector,
                    timeout_seconds=_LIST_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            ns_label = namespace if namespace is not None else "*"
            if _is_not_found(exc):
                # The CRD is not installed (yet).  Say so once per kind.
                key = f"crd_missing:{resource}"
                log = self._logger.debug if key in self._warned else self._logger.warning
                self._warned.add(key)
                log(
                    "Orphan GC: custom resource %s is not served by the cluster; "
                    "skipping it (namespace=%s)",
                    resource,
                    ns_label,
                )
                return []
            self.stats.last_error = f"list {resource} failed: {exc}"
            self._logger.warning(
                "Orphan GC: list %s failed for namespace=%s: %s",
                resource,
                ns_label,
                exc,
                exc_info=True,
            )
            return []
        items = response.get("items") if isinstance(response, dict) else None
        return [item for item in items or [] if isinstance(item, dict)]

    @staticmethod
    def _classify_custom_orphan(
        obj: dict[str, Any],
        *,
        resource: K8sCustomResource,
        namespace: Optional[str],
        known_ids: set[str],
        request_id_label: str,
        instance_label: str,
        instance_id: str,
    ) -> Optional[OrphanCustomResource]:
        metadata = obj.get("metadata")
        if not isinstance(metadata, dict):
            return None
        name = metadata.get("name")
        if not name:
            return None
        if metadata.get("deletionTimestamp"):
            return None
        labels = dict(metadata.get("labels") or {})
        if not instance_id or labels.get(instance_label) != instance_id:
            return None
        request_id = labels.get(request_id_label)
        if request_id is not None and request_id in known_ids:
            return None
        creation_timestamp = metadata.get("creationTimestamp")
        return OrphanCustomResource(
            resource=resource,
            name=str(name),
            namespace=str(metadata.get("namespace") or namespace or ""),
            request_id=request_id,
            creation_timestamp=(
                str(creation_timestamp) if creation_timestamp is not None else None
            ),
        )

    @staticmethod
    def _classify_orphan(
        pod: V1Pod,
        *,
        namespace: Optional[str],
        known_ids: set[str],
        request_id_label: str,
        instance_label: str,
        instance_id: str,
    ) -> Optional[OrphanPod]:
        metadata = getattr(pod, "metadata", None)
        if metadata is None:
            return None
        pod_name = getattr(metadata, "name", None)
        if not pod_name:
            return None
        labels = dict(getattr(metadata, "labels", None) or {})
        if not instance_id or labels.get(instance_label) != instance_id:
            return None
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

    async def _delete_orphans_concurrently(self, orphans: list[Orphan]) -> None:
        """Delete *orphans* concurrently using :func:`asyncio.gather`.

        Each delete runs inside :func:`asyncio.to_thread` (the k8s client is
        synchronous) under a shared :class:`asyncio.Semaphore` capped at
        ``_DELETE_CONCURRENCY`` so the apiserver is not flooded.

        Per-orphan failures are logged at WARNING and do not abort the
        remaining deletes (``return_exceptions=True``).  Stats
        (``total_orphans_deleted``, ``delete_failures``) are updated inside
        the existing :meth:`_delete_orphan` helper, which is GIL-safe for
        simple integer increments.
        """
        sem = asyncio.Semaphore(_DELETE_CONCURRENCY)

        async def _bounded_delete(orphan: Orphan) -> None:
            async with sem:
                if isinstance(orphan, OrphanCustomResource):
                    await asyncio.to_thread(self._delete_custom_orphan, orphan)
                else:
                    await asyncio.to_thread(self._delete_orphan, orphan)

        await asyncio.gather(
            *(_bounded_delete(orphan) for orphan in orphans),
            return_exceptions=True,
        )

    def _may_delete(
        self, name: str, request_id: Optional[str], creation_timestamp: Optional[str]
    ) -> bool:
        """Apply the guards every orphan delete must pass.

        The min-age guard prevents deletion of objects whose request record
        may not yet have been committed to storage (in-flight request commit
        race); the re-check confirms the request is still missing right now.
        """
        min_age = self._config.orphan_min_age_seconds
        if min_age > 0 and creation_timestamp is not None:
            age_seconds = _pod_age_seconds(creation_timestamp)
            if age_seconds is not None and age_seconds < min_age:
                self._logger.debug(
                    "Skipping orphan %s; created %.1fs ago, under min-age threshold (%ds)",
                    name,
                    age_seconds,
                    min_age,
                )
                return False
        if not request_id:
            return False
        if not self._request_still_unknown(request_id):
            with self._stats_lock:
                self.stats.skipped_recheck += 1
            self._logger.info(
                "Keeping %s: request %s exists in storage or could not be confirmed missing",
                name,
                request_id,
            )
            return False
        return True

    def _delete_custom_orphan(self, orphan: OrphanCustomResource) -> None:
        """Best-effort delete of an orphaned custom resource; swallow 404."""
        label = f"{orphan.resource} {orphan.namespace}/{orphan.name}"
        if not self._may_delete(label, orphan.request_id, orphan.creation_timestamp):
            return
        try:
            self._client.custom_objects_api.delete_namespaced_custom_object(
                group=orphan.resource.group,
                version=orphan.resource.version,
                namespace=orphan.namespace,
                plural=orphan.resource.plural,
                name=orphan.name,
            )
            with self._stats_lock:
                self.stats.total_orphans_deleted += 1
            self._logger.info(
                "Orphan custom resource deleted: kind=%s name=%s namespace=%s request_id=%s",
                orphan.resource,
                orphan.name,
                orphan.namespace,
                orphan.request_id,
            )
        except Exception as exc:
            if _is_not_found(exc):
                with self._stats_lock:
                    self.stats.total_orphans_deleted += 1
                self._logger.debug("Orphan %s already gone (404)", label)
                return
            with self._stats_lock:
                self.stats.delete_failures += 1
            self.stats.last_error = f"delete {label}: {exc}"
            self._logger.warning("Orphan custom resource delete failed: %s error=%s", label, exc)

    def _delete_orphan(self, orphan: OrphanPod) -> None:
        """Best-effort delete; swallow 404 (already gone)."""
        if not self._may_delete(orphan.pod_name, orphan.request_id, orphan.creation_timestamp):
            return
        try:
            self._client.core_v1.delete_namespaced_pod(
                name=orphan.pod_name,
                namespace=orphan.namespace,
            )
            with self._stats_lock:
                self.stats.total_orphans_deleted += 1
            self._logger.info(
                "Orphan pod deleted: pod=%s namespace=%s request_id=%s",
                orphan.pod_name,
                orphan.namespace,
                orphan.request_id,
            )
        except Exception as exc:
            if _is_not_found(exc):
                # Already gone — treat as success for accounting.
                with self._stats_lock:
                    self.stats.total_orphans_deleted += 1
                self._logger.debug("Orphan pod %s already gone (404)", orphan.pod_name)
                return
            with self._stats_lock:
                self.stats.delete_failures += 1
            self.stats.last_error = f"delete pod {orphan.pod_name}: {exc}"
            self._logger.warning(
                "Orphan pod delete failed: pod=%s namespace=%s error=%s",
                orphan.pod_name,
                orphan.namespace,
                exc,
            )


def _pod_age_seconds(creation_timestamp: str) -> Optional[float]:
    """Return how many seconds ago the pod was created, or ``None`` on parse failure.

    The kubernetes client serialises ``V1ObjectMeta.creation_timestamp`` as an
    ISO 8601 string.  We normalise the ``Z`` suffix to ``+00:00`` so that
    :meth:`datetime.datetime.fromisoformat` works on Python 3.10 as well as 3.11+.
    """
    try:
        ts_str = creation_timestamp.replace("Z", "+00:00")
        created = datetime.datetime.fromisoformat(ts_str)
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        delta = (now - created).total_seconds()
        return delta
    except (ValueError, TypeError):
        return None


def _is_not_found(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` is a 404 ``ApiException``."""
    try:
        from kubernetes.client.exceptions import ApiException
    except ImportError:
        return False
    if not isinstance(exc, ApiException):
        return False
    return getattr(exc, "status", None) == 404


__all__ = ["Orphan", "OrphanCustomResource", "OrphanGCStats", "OrphanGarbageCollector"]

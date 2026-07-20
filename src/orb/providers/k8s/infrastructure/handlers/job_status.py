"""Job status resolver — read path for :class:`K8sJobHandler`.

Encapsulates the ``check_hosts_status`` flow for the Job handler:

* Cache-first read via :meth:`K8sHandlerBase._read_from_cache`.
* Fallback list of pods via ``CoreV1Api.list_namespaced_pod``.
* Always rebase the verdict on the Job controller's
  ``active`` / ``succeeded`` / ``failed`` / ``conditions`` so the
  rollup reflects the authoritative run-to-completion signal.

The resolver is constructed by :class:`K8sJobHandler` and reuses the
handler's existing helpers (retry, label selector, cache read,
instance-dict mapping) — it carries no state of its own.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler


class JobStatusResolver:
    """Compute the per-request status verdict for a Job-managed pod set."""

    def __init__(self, handler: K8sJobHandler) -> None:
        self._handler = handler
        # Per-workload TTL cache:
        #   (namespace, job_name) -> (view, fetch_monotonic_ts)
        # Guarded by _cache_lock so concurrent check_hosts_status calls are safe.
        self._controller_cache: dict[tuple[str, str], tuple[dict[str, Any], float]] = {}
        self._cache_lock = threading.Lock()

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Return per-pod details + the Job-driven fulfilment verdict.

        Read path:

        1. Cache-first — if a :class:`PodStateCache` has been injected
           and the watcher reports alive, build the per-pod instance
           list from the cached states.  Stale entries are dropped
           transparently.
        2. Fallback — list the request's pods via
           ``list_namespaced_pod(label_selector=...)``.
        3. Always — read the Job object itself for
           ``active`` / ``succeeded`` / ``failed`` / ``conditions`` so
           the verdict reflects the controller's view.
        """
        handler = self._handler
        namespace = handler._resolve_request_namespace(request)
        job_name = handler._resolve_job_name(request)

        cached = handler._read_from_cache(request)
        if cached is not None:
            # When the cache served the per-pod list, still rebase the
            # fulfilment verdict on the Job status because the
            # controller's view is authoritative for run-to-completion
            # semantics.
            cached_instances = handler.apply_pod_timeouts(list(cached.instances))
            controller_view = self._get_cached_job_status(namespace, job_name)
            fulfilment = self.compute_fulfilment(
                cached_instances,
                request.requested_count,
                controller_view=controller_view,
            )
            return CheckHostsStatusResult(instances=cached_instances, fulfilment=fulfilment)

        selector = handler.build_label_selector(request)

        try:
            # resource_version='0' serves from the apiserver reflector cache
            # (sub-ms, ~500 ms staleness) instead of reading from etcd.
            # Acceptable for status polls — not used on create/update paths.
            with handler._timed_api_call("list_namespaced_pod"):
                response = handler.with_retry(
                    handler.client.core_v1.list_namespaced_pod,
                    namespace=namespace,
                    label_selector=selector,
                    resource_version="0",
                    operation_name="list_namespaced_pod",
                )
        except Exception as exc:
            handler._record_api_exception(exc, operation="list_namespaced_pod")
            handler._logger.error(
                "list_namespaced_pod failed for job request %s: %s",
                request.request_id,
                exc,
                exc_info=True,
            )
            return CheckHostsStatusResult(
                instances=[],
                fulfilment=ProviderFulfilment(
                    state="in_progress",
                    message=f"Kubernetes list failed (will retry): {exc}",
                    target_units=request.requested_count,
                    running_count=0,
                    pending_count=0,
                    failed_count=0,
                ),
            )

        pods: list[Any] = list(getattr(response, "items", []) or [])
        instances: list[dict[str, Any]] = [
            handler._instance_dict_for_pod(pod, namespace=namespace) for pod in pods
        ]
        instances = handler.apply_pod_timeouts(instances)
        controller_view = self._get_cached_job_status(namespace, job_name)
        fulfilment = self.compute_fulfilment(
            instances,
            request.requested_count,
            controller_view=controller_view,
        )
        return CheckHostsStatusResult(instances=instances, fulfilment=fulfilment)

    @staticmethod
    def _is_terminal(view: dict[str, Any]) -> bool:
        """Return True when the Job has reached a terminal condition.

        A Job is terminal when it has a ``Complete`` or ``Failed`` condition
        with ``status=True``.  Both states are equally steady — neither
        transitions further — so both are safe to cache.

        Caching ``Failed`` prevents re-fetching the Job on every poll after it
        fails (Finding 3: re-fetch loop / log flood fix).
        """
        return any(
            (c.get("type") in ("Complete", "Failed") and c.get("status") == "True")
            for c in (view.get("conditions") or [])
            if isinstance(c, dict)
        )

    def _get_cached_job_status(self, namespace: str, job_name: str) -> dict[str, Any]:
        """Return the controller view, serving from the TTL cache when fresh.

        Cache semantics:

        * **TTL disabled** — when ``controller_status_cache_ttl_seconds <= 0``
          the cache is bypassed entirely (no store, no read) to avoid
          unbounded dict growth during high-frequency polls (Finding 5).
        * **Terminal-state gate** — the cached view is only served when the
          Job has reached a terminal condition (``Complete`` OR ``Failed``).
          While the job is still running, every poll issues a fresh GET so
          the active→complete/failed transition is observed promptly.
          Caching ``Failed`` avoids the re-fetch loop that floods logs when
          the Job never recovers (Finding 3).
        * **TOCTOU guard** — the fetch timestamp is captured *before* the
          blocking GET.  On store, an existing entry is only overwritten when
          its timestamp is older than the new one, so a slow thread cannot
          clobber a fresher entry with a stale view (Finding 2).

        The TTL is controlled by
        ``K8sProviderConfig.controller_status_cache_ttl_seconds`` (default 5 s).
        """
        ttl = self._handler.config.controller_status_cache_ttl_seconds

        # TTL <= 0 means disabled — bypass cache entirely (Finding 5).
        if ttl <= 0:
            return self.read_job_status(namespace, job_name)

        cache_key = (namespace, job_name)
        now = time.monotonic()

        with self._cache_lock:
            entry = self._controller_cache.get(cache_key)
            if entry is not None:
                view, ts = entry
                if self._is_terminal(view) and now - ts < ttl:
                    return view

        # Cache miss, expired, or job not yet terminal — fetch outside the lock
        # to avoid blocking concurrent callers during the API GET.  Capture
        # the timestamp BEFORE the GET (Finding 2 / TOCTOU guard).
        fetch_ts = time.monotonic()
        view = self.read_job_status(namespace, job_name)

        with self._cache_lock:
            existing = self._controller_cache.get(cache_key)
            # Only overwrite if there is no existing entry or the existing entry
            # is older than this fetch (Finding 2: don't clobber a newer entry).
            if existing is None or existing[1] < fetch_ts:
                self._controller_cache[cache_key] = (view, fetch_ts)

        return view

    def read_job_status(self, namespace: str, job_name: str) -> dict[str, Any]:
        """Read controller view from ``batch/v1 Job.status``.

        Returned shape (all keys optional):

        * ``active``     — number of currently active pods
        * ``succeeded``  — number of pods that completed successfully
        * ``failed``     — number of pods that terminated with failure
        * ``conditions`` — list of ``{type, status, reason}`` dicts
          (``Complete`` and ``Failed`` are the two terminal types)

        Missing fields default to ``None`` / empty so the caller can
        fall back to the pod-roll-up math without special-casing.
        """
        handler = self._handler
        try:
            with handler._timed_api_call("read_namespaced_job"):
                job = handler.with_retry(
                    handler.client.batch_v1.read_namespaced_job,
                    name=job_name,
                    namespace=namespace,
                    operation_name="read_namespaced_job",
                )
        except Exception as exc:
            if handler.is_not_found(exc):
                # Job not found is normal after release or before creation.
                # Log at debug to avoid flooding on the terminal-state poll
                # path where the Job has already been deleted (Finding 3).
                handler._logger.debug(
                    "Job %s in %s not found — assuming pre-create or post-release",
                    job_name,
                    namespace,
                )
                return {}
            handler._record_api_exception(exc, operation="read_namespaced_job")
            handler._logger.warning(
                "read_namespaced_job failed (job=%s namespace=%s): %s",
                job_name,
                namespace,
                exc,
                exc_info=True,
            )
            return {}

        status = getattr(job, "status", None)
        if status is None:
            return {}

        conditions_list: list[dict[str, Any]] = []
        for cond in getattr(status, "conditions", None) or []:
            conditions_list.append(
                {
                    "type": getattr(cond, "type", None),
                    "status": getattr(cond, "status", None),
                    "reason": getattr(cond, "reason", None),
                    "message": getattr(cond, "message", None),
                }
            )

        return {
            "active": getattr(status, "active", None),
            "succeeded": getattr(status, "succeeded", None),
            "failed": getattr(status, "failed", None),
            "conditions": conditions_list,
        }

    def compute_fulfilment(
        self,
        instances: list[dict[str, Any]],
        requested_count: int,
        *,
        controller_view: Optional[dict[str, Any]] = None,
    ) -> ProviderFulfilment:
        """Roll up per-pod statuses + Job status into a verdict.

        Decision precedence:

        1. ``Complete`` condition on the Job → ``fulfilled``
           (irrespective of pod phase — succeeded pods are gone but the
           Job is complete).
        2. ``Failed`` condition on the Job → ``failed``.
        3. Otherwise, use the controller's ``succeeded`` count when
           available, falling back to the pod-roll-up math from the
           per-pod ``running`` / ``pending`` / ``failed`` counts.
        """
        controller_view = controller_view or {}
        succeeded = controller_view.get("succeeded")
        failed_controller = controller_view.get("failed")
        active = controller_view.get("active")
        conditions = controller_view.get("conditions") or []

        # Job-level conditions: ``Complete`` (status=True) and
        # ``Failed`` (status=True) are the two terminal job conditions.
        for cond in conditions:
            ctype = cond.get("type") if isinstance(cond, dict) else None
            cstatus = cond.get("status") if isinstance(cond, dict) else None
            if ctype == "Complete" and cstatus == "True":
                target = max(requested_count, 0)
                effective_succeeded = int(succeeded) if isinstance(succeeded, int) else target
                return ProviderFulfilment(
                    state="fulfilled",
                    message=f"Job complete: {effective_succeeded}/{target} succeeded",
                    target_units=target,
                    fulfilled_units=effective_succeeded,
                    running_count=effective_succeeded,
                    pending_count=0,
                    failed_count=int(failed_controller)
                    if isinstance(failed_controller, int)
                    else 0,
                )
            if ctype == "Failed" and cstatus == "True":
                return ProviderFulfilment(
                    state="failed",
                    message=f"Job failed: {cond.get('reason', 'unknown')}",
                    target_units=max(requested_count, 0),
                    fulfilled_units=int(succeeded) if isinstance(succeeded, int) else 0,
                    running_count=int(succeeded) if isinstance(succeeded, int) else 0,
                    pending_count=0,
                    failed_count=int(failed_controller)
                    if isinstance(failed_controller, int)
                    else 0,
                )

        running_count = sum(1 for i in instances if i.get("status") == "running")
        pending_count = sum(1 for i in instances if i.get("status") in ("pending", "starting"))
        pod_failed_count = sum(1 for i in instances if i.get("status") == "failed")

        # When the controller exposes a ``succeeded`` count, prefer it
        # for the ``running`` (i.e. counted-towards-target) tally.
        effective_running = int(succeeded) if isinstance(succeeded, int) else running_count
        effective_failed = (
            int(failed_controller) if isinstance(failed_controller, int) else pod_failed_count
        )
        effective_pending = (
            int(active) - effective_running if isinstance(active, int) else pending_count
        )
        effective_pending = max(effective_pending, 0)

        if effective_running >= requested_count and effective_failed == 0 and requested_count > 0:
            return ProviderFulfilment(
                state="fulfilled",
                message=f"Job complete: {effective_running}/{requested_count} succeeded",
                target_units=requested_count,
                fulfilled_units=effective_running,
                running_count=effective_running,
                pending_count=effective_pending,
                failed_count=effective_failed,
            )
        if effective_pending > 0 or (
            isinstance(active, int) and active > 0 and effective_running < requested_count
        ):
            return ProviderFulfilment(
                state="in_progress",
                message=(
                    f"Job running: {effective_running}/{requested_count} succeeded, "
                    f"{effective_pending} active"
                ),
                target_units=requested_count,
                fulfilled_units=effective_running,
                running_count=effective_running,
                pending_count=effective_pending,
                failed_count=effective_failed,
            )
        if effective_failed > 0 and effective_running == 0 and requested_count > 0:
            return ProviderFulfilment(
                state="failed",
                message=f"All {effective_failed} pod(s) failed",
                target_units=requested_count,
                fulfilled_units=0,
                running_count=0,
                pending_count=effective_pending,
                failed_count=effective_failed,
            )
        if effective_running > 0:
            return ProviderFulfilment(
                state="partial",
                message=f"Job partial: {effective_running}/{requested_count} succeeded",
                target_units=requested_count,
                fulfilled_units=effective_running,
                running_count=effective_running,
                pending_count=effective_pending,
                failed_count=effective_failed,
            )
        return ProviderFulfilment(
            state="in_progress",
            message="Job starting",
            target_units=requested_count,
            fulfilled_units=0,
            running_count=effective_running,
            pending_count=effective_pending,
            failed_count=effective_failed,
        )


__all__ = ["JobStatusResolver"]

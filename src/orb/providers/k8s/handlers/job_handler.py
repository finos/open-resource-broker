"""K8sJobHandler — ``batch/v1 Job`` provisioning handler.

Run-to-completion semantics — Job controller
============================================

A ``batch/v1 Job`` is the Kubernetes-native primitive for
run-to-completion workloads.  The Job controller spawns
``spec.parallelism`` pods concurrently and the Job is ``Complete`` once
``spec.completions`` pods exit ``0``.  The handler maps one ORB request
to one Job with ``parallelism = completions = N`` so every requested
unit must run successfully to completion.

Crucial invariants
------------------

* ``backoffLimit = 0`` — ORB owns retry semantics at the *request*
  level.  The Job controller must NOT silently restart failed pods.
* ``parallelism`` cannot be safely mutated post-creation.  The Job
  controller does honour patches to ``spec.parallelism`` for live Jobs
  (since k8s 1.21), but the semantics around ``completions``,
  in-progress pods, and the ``Complete`` condition are subtle enough
  that **selective release is not supported**.  ``release_hosts`` deletes
  the entire Job (cascade-deletes pods) regardless of how many
  ``machine_ids`` the caller passes.
* Pod-level ``restartPolicy = Never`` is required when ``backoffLimit=0``
  (the controller validates it).  This is consistent with the
  stand-alone Pod handler's invariants.

Phase scope
-----------

* ``acquire_hosts``    — creates one Job with
  ``spec.parallelism=spec.completions=request.requested_count`` and a
  pod template inheriting the full ORB label set.
* ``check_hosts_status`` — lists pods via the request-id label selector
  (cache-first when a watcher is wired) and reads back
  ``active`` / ``succeeded`` / ``failed`` / ``conditions`` from the
  Job status for the rollup verdict.
* ``release_hosts``    — deletes the Job (cascade-deletes pods).  The
  ``machine_ids`` argument is informational only; selective release is
  not supported and the handler logs the requested IDs at info level for
  audit.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.utilities.job_spec import (
    build_job_spec,
    make_job_name,
)
from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache


@injectable
class K8sJobHandler(K8sHandlerBase):
    """Handler for the ``Job`` provider-API key.

    One ORB capacity unit equals one pod under a single Job
    (``batch/v1``).  Selective termination is NOT supported by this
    handler — see the module docstring for the rationale.
    """

    PROVIDER_API: str = "Job"

    def __init__(
        self,
        kubernetes_client: K8sClient,
        config: K8sProviderConfig,
        logger: LoggingPort,
        *,
        pod_state_cache: Optional[PodStateCache] = None,
        cache_alive: Optional[Callable[[], bool]] = None,
        stale_cache_timeout_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(
            kubernetes_client=kubernetes_client,
            config=config,
            logger=logger,
        )
        # Cache wiring matches the Pod / Deployment / StatefulSet
        # handlers: when the watcher is alive and the cache has entries
        # for the request, the read path skips the list call.
        self._pod_state_cache = pod_state_cache
        self._cache_alive = cache_alive
        self._stale_cache_timeout_seconds = (
            stale_cache_timeout_seconds
            if stale_cache_timeout_seconds is not None
            else float(config.stale_cache_timeout_seconds)
        )

    # ------------------------------------------------------------------
    # acquire_hosts
    # ------------------------------------------------------------------

    async def acquire_hosts(self, request: Request, template: Template) -> dict[str, Any]:
        """Create a single Job with ``parallelism = completions = N``.

        The Job is named ``orb-{request_id[:8]}``.  Pod names are
        stamped by the controller as ``orb-{request_id[:8]}-<random>``
        and are NOT known at acquire time — the strategy resolves them
        later via :meth:`check_hosts_status`.

        Returns a dict consumed by the strategy's ``acquire`` to build
        the :class:`Accepted` outcome.  ``resource_ids`` is the
        single-element list ``[job_name]``; ``machine_ids`` is empty at
        acquire time because the controller has not yet stamped pod
        names.  ``provider_data`` carries the namespace, Job name and
        the requested parallelism so the release / status paths can
        recover context without re-querying.
        """
        namespace = self.resolve_namespace(template)
        parallelism = max(int(request.requested_count), 1)
        job_name = make_job_name(str(request.request_id))

        self._logger.info(
            "Kubernetes job acquire: request_id=%s namespace=%s job=%s parallelism=%s",
            request.request_id,
            namespace,
            job_name,
            parallelism,
        )

        body = build_job_spec(
            template,
            request,
            job_name=job_name,
            namespace=namespace,
            parallelism=parallelism,
            provider_api=self.PROVIDER_API,
            config=self._config,
        )

        await asyncio.to_thread(
            self.with_retry,
            self.client.batch_v1.create_namespaced_job,
            namespace=namespace,
            body=body,
            operation_name="create_namespaced_job",
        )

        return {
            "resource_ids": [job_name],
            "machine_ids": [],
            "provider_data": {
                "namespace": namespace,
                "job_name": job_name,
                "parallelism": parallelism,
            },
        }

    # ------------------------------------------------------------------
    # check_hosts_status
    # ------------------------------------------------------------------

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
        namespace = self._resolve_request_namespace(request)
        job_name = self._resolve_job_name(request)

        cached = self._read_from_cache(request)
        if cached is not None:
            # When the cache served the per-pod list, still rebase the
            # fulfilment verdict on the Job status because the
            # controller's view is authoritative for run-to-completion
            # semantics.
            cached_instances = self.apply_pod_timeouts(list(cached.instances))
            controller_view = self._read_job_status(namespace, job_name)
            fulfilment = self._compute_fulfilment(
                cached_instances,
                request.requested_count,
                controller_view=controller_view,
            )
            return CheckHostsStatusResult(instances=cached_instances, fulfilment=fulfilment)

        selector = self.build_label_selector(request)

        try:
            response = self.with_retry(
                self.client.core_v1.list_namespaced_pod,
                namespace=namespace,
                label_selector=selector,
                operation_name="list_namespaced_pod",
            )
        except Exception as exc:
            self._logger.error(
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
            self._instance_dict_for_pod(pod, namespace=namespace) for pod in pods
        ]
        instances = self.apply_pod_timeouts(instances)
        controller_view = self._read_job_status(namespace, job_name)
        fulfilment = self._compute_fulfilment(
            instances,
            request.requested_count,
            controller_view=controller_view,
        )
        return CheckHostsStatusResult(instances=instances, fulfilment=fulfilment)

    def _read_from_cache(self, request: Request) -> Optional[CheckHostsStatusResult]:
        """Cache-first read path; mirrors the other handlers' logic."""
        cache = self._pod_state_cache
        if cache is None:
            return None
        if self._cache_alive is not None and not self._cache_alive():
            return None

        request_id = str(request.request_id)
        dropped = cache.mark_stale(request_id, self._stale_cache_timeout_seconds)
        if dropped:
            self._logger.debug(
                "Dropped %s stale pod cache entr%s for job request %s",
                len(dropped),
                "y" if len(dropped) == 1 else "ies",
                request_id,
            )

        states = cache.get(request_id)
        if states is None:
            return None

        instances = [self._instance_dict_for_state(state) for state in states]
        # Caller (``check_hosts_status``) rebases the fulfilment on the
        # controller view; here we just need the per-pod instance list.
        return CheckHostsStatusResult(
            instances=instances,
            fulfilment=ProviderFulfilment(
                state="in_progress",
                message="placeholder (rebased by caller)",
                target_units=request.requested_count,
            ),
        )

    def _read_job_status(self, namespace: str, job_name: str) -> dict[str, Any]:
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
        try:
            job = self.with_retry(
                self.client.batch_v1.read_namespaced_job,
                name=job_name,
                namespace=namespace,
                operation_name="read_namespaced_job",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                self._logger.debug(
                    "Job %s in %s not found — assuming pre-create or post-release",
                    job_name,
                    namespace,
                )
                return {}
            self._logger.warning(
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

    def _instance_dict_for_state(self, state: PodState) -> dict[str, Any]:
        """Convert a cached :class:`PodState` into the instance-dict shape."""
        return {
            "instance_id": state.pod_name,
            "resource_id": state.pod_name,
            "name": state.pod_name,
            "status": state.status,
            "status_reason": state.status_reason,
            "private_ip": state.pod_ip,
            "public_ip": state.host_ip,
            "launch_time": state.start_time,
            "instance_type": "",
            "image_id": "",
            "subnet_id": None,
            "security_group_ids": [],
            "vpc_id": None,
            "tags": dict(state.labels),
            "price_type": None,
            "provider_api": self.PROVIDER_API,
            "provider_data": {
                "namespace": state.namespace,
                "node_name": state.node_name,
                "phase": state.phase,
                "ready": state.ready,
            },
            "metadata": {},
        }

    def _instance_dict_for_pod(self, pod: Any, namespace: str) -> dict[str, Any]:
        """Convert a ``V1Pod`` to the per-instance dict shape ORB expects."""
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

        ready = self._is_pod_ready(conditions)
        status_str = self._pod_status_string(phase, ready)
        status_reason = self._extract_status_reason(container_statuses, conditions)

        return {
            "instance_id": name,
            "resource_id": name,
            "name": name,
            "status": status_str,
            "status_reason": status_reason,
            "private_ip": pod_ip,
            "public_ip": host_ip,
            "launch_time": str(start_time) if start_time is not None else None,
            "instance_type": "",
            "image_id": "",
            "subnet_id": None,
            "security_group_ids": [],
            "vpc_id": None,
            "tags": labels,
            "price_type": None,
            "provider_api": self.PROVIDER_API,
            "provider_data": {
                "namespace": namespace,
                "node_name": node_name,
                "phase": phase,
                "ready": ready,
            },
            "metadata": {},
        }

    @staticmethod
    def _is_pod_ready(conditions: list[Any]) -> bool:
        for cond in conditions:
            ctype = getattr(cond, "type", None)
            cstatus = getattr(cond, "status", None)
            if ctype == "Ready" and cstatus == "True":
                return True
        return False

    @staticmethod
    def _pod_status_string(phase: Optional[str], ready: bool) -> str:
        """Map ``pod.status.phase`` (+ readiness) to an ORB instance status.

        Note: a Job pod that exits ``0`` is ``Succeeded``; we surface
        that as ``"running"`` to match the rest of the kubernetes
        provider's status surface (a successful run-to-completion is the
        end state ORB callers care about — the fulfilment verdict carries
        the terminal-vs-active distinction via the Job controller view).
        """
        if phase == "Running":
            return "running" if ready else "starting"
        if phase == "Succeeded":
            return "running"
        if phase == "Failed":
            return "failed"
        return "pending"

    @staticmethod
    def _extract_status_reason(
        container_statuses: list[Any],
        conditions: list[Any],
    ) -> Optional[str]:
        """Best-effort extraction of a human-readable status reason."""
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

    def _compute_fulfilment(
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

    # ------------------------------------------------------------------
    # release_hosts
    # ------------------------------------------------------------------

    async def release_hosts(
        self,
        machine_ids: list[str],
        request: Request,
    ) -> None:
        """Delete the whole Job (cascade-deletes pods).

        Selective release is **not supported** for Jobs — ``parallelism``
        cannot be safely mutated post-creation given ORB's
        ``backoffLimit=0`` invariant.  Any call to ``release_hosts``
        deletes the entire Job; ``machine_ids`` is logged for audit but
        not honoured selectively.

        The Job is deleted with ``propagation_policy='Background'`` so
        the API call returns immediately and the controller cleans up
        the owned pods asynchronously.

        Args:
            machine_ids: Pod names the caller wanted to release.  Logged
                at info level for audit; not used for selective release.
            request: Request providing namespace + job-name context via
                ``provider_data`` (falls back to deterministic defaults).
        """
        if not machine_ids:
            self._logger.debug(
                "release_hosts called with no machine_ids for job request %s — no-op",
                request.request_id,
            )
            return

        namespace = self._resolve_request_namespace(request)
        job_name = self._resolve_job_name(request)

        self._logger.info(
            "Kubernetes job release: request_id=%s namespace=%s job=%s "
            "requested_machine_ids=%s (deleting whole Job — selective release not supported)",
            request.request_id,
            namespace,
            job_name,
            machine_ids,
        )

        await self._delete_job(namespace, job_name)

    async def _delete_job(self, namespace: str, job_name: str) -> None:
        """Delete the Job with background propagation.

        Background propagation lets the API return immediately and the
        controller cleans up the owned pods asynchronously.  404s are
        best-effort — a Job that already evaporated is fine.
        """
        try:
            await asyncio.to_thread(
                self.client.batch_v1.delete_namespaced_job,
                name=job_name,
                namespace=namespace,
                propagation_policy="Background",
            )
            return
        except Exception as exc:
            if self.is_not_found(exc):
                self._logger.debug(
                    "Job %s in %s already gone (404) — delete is a no-op",
                    job_name,
                    namespace,
                )
                return
            self._logger.debug(
                "Initial delete failed for job=%s in %s; retrying: %s",
                job_name,
                namespace,
                exc,
            )

        try:
            await asyncio.to_thread(
                self.with_retry,
                self.client.batch_v1.delete_namespaced_job,
                name=job_name,
                namespace=namespace,
                propagation_policy="Background",
                operation_name="delete_namespaced_job",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                return
            self._logger.warning(
                "Failed to delete job=%s namespace=%s: %s",
                job_name,
                namespace,
                exc,
            )
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_request_namespace(self, request: Request) -> str:
        """Resolve a request's namespace using saved provider_data when present."""
        provider_data = getattr(request, "provider_data", None) or {}
        if isinstance(provider_data, dict):
            ns = provider_data.get("namespace")
            if isinstance(ns, str) and ns:
                return ns
        return self._config.namespace

    def _resolve_job_name(self, request: Request) -> str:
        """Recover the Job name created at acquire time.

        Persisted in ``request.provider_data["job_name"]`` by
        :meth:`acquire_hosts`; falls back to the deterministic
        :func:`make_job_name` when the field is missing so callers that
        operate on a freshly-loaded Request still resolve a sensible
        value.
        """
        provider_data = getattr(request, "provider_data", None) or {}
        if isinstance(provider_data, dict):
            name = provider_data.get("job_name")
            if isinstance(name, str) and name:
                return name
        return make_job_name(str(request.request_id))

    # ------------------------------------------------------------------
    # Examples
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Return one example template that submits as a ``Job``."""
        return [
            Template(
                template_id="k8s-job-example",
                name="Kubernetes Job example",
                description="Submit a run-to-completion Job via the kubernetes provider.",
                provider_type="k8s",
                provider_api="Job",
                image_id="busybox:latest",
                max_instances=3,
                provider_data={
                    "k8s": {
                        "container_image": "busybox:latest",
                        "resource_requests": {"cpu": "100m", "memory": "128Mi"},
                        "resource_limits": {"cpu": "500m", "memory": "256Mi"},
                        "command": ["sh", "-c", "echo done"],
                    },
                },
            ),
        ]


__all__ = [
    "K8sJobHandler",
]

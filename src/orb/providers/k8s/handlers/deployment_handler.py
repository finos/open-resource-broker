"""K8sDeploymentHandler — ``apps/v1 Deployment`` provisioning handler.

Selective termination mechanism — ``controller.kubernetes.io/pod-deletion-cost``
============================================================================

A Deployment is owned by its ``ReplicaSet`` controller: deleting a pod
directly causes the controller to re-create it, which would defeat
ORB's ``release_hosts(machine_ids=[...])`` contract.

The Kubernetes-native solution is the
``controller.kubernetes.io/pod-deletion-cost`` annotation.  When the
ReplicaSet controller scales a Deployment down, it sorts the pod set by
deletion cost (ascending) and removes the lowest-cost pods first.
Default cost is ``0``; pods we want removed first are annotated with a
large negative integer (we use ``"-9999"`` — well below the default and
small enough to fit in the int32 range the controller expects).

The annotation is **stable** since Kubernetes 1.22 (originally beta in
1.21).  The same annotation is honoured by the StatefulSet controller
for scale-down ordering.

Reference: Kubernetes documentation,
"ReplicaSet — Pod deletion cost"
(https://kubernetes.io/docs/concepts/workloads/controllers/replicaset/#pod-deletion-cost).

Release sequence
----------------

For a selective release ``release_hosts(machine_ids=[m1, m2])``:

1. Patch each victim pod with annotation
   ``controller.kubernetes.io/pod-deletion-cost: "-9999"`` — strategic-
   merge patch keeps existing annotations intact.
2. Patch ``spec.replicas`` to ``current_replicas - len(machine_ids)``.
   The controller chooses the annotated victims because they have the
   lowest deletion cost in the set.

For a full release (``machine_ids`` covers every pod for the request
*or* the caller passes the deployment-name shortcut), step 1 is skipped
and ``spec.replicas`` is patched directly to ``0``; the Deployment is
then deleted entirely so the request leaves behind no idle controller.

The handler does **not** delete pods directly — it always leaves the
controller in charge of the actual termination so that the
``last_pod_ready_seconds``/PDB invariants remain honoured.

Phase scope
-----------

* ``acquire_hosts``    — creates one Deployment with
  ``spec.replicas=request.requested_count`` and a pod template inheriting
  the full ORB label set.
* ``check_hosts_status`` — lists pods via the request-id label selector
  (cache-first when a watcher is wired) and reads back
  ``availableReplicas`` / ``readyReplicas`` / ``conditions`` from the
  Deployment status for the rollup verdict.
* ``release_hosts``    — selective via pod-deletion-cost + replicas
  patch; full-release via replicas patch to zero + Deployment delete.
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
from orb.providers.k8s.utilities.deployment_spec import (
    build_deployment_spec,
    make_deployment_name,
)
from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache

# Annotation key + default victim value used during selective release.
# ``"-9999"`` is small enough to fit in int32 and large enough in
# absolute terms to beat any default deletion cost in the surviving pod
# set (the controller default is ``0``).
POD_DELETION_COST_ANNOTATION = "controller.kubernetes.io/pod-deletion-cost"
VICTIM_DELETION_COST = "-9999"

# Cap on concurrent annotation patches during selective release.  The
# kubernetes apiserver can throttle patch requests; 50 mirrors the cap
# used by ``K8sPodHandler`` for create / delete operations.
_MAX_CONCURRENT_PATCHES = 50


@injectable
class K8sDeploymentHandler(K8sHandlerBase):
    """Handler for the ``Deployment`` provider-API key.

    One ORB capacity unit equals one pod under a single Deployment
    (``apps/v1``).  Selective termination is performed via the
    ``controller.kubernetes.io/pod-deletion-cost`` annotation — see the
    module docstring for the mechanism.
    """

    PROVIDER_API: str = "Deployment"

    def __init__(
        self,
        kubernetes_client: K8sClient,
        config: K8sProviderConfig,
        logger: LoggingPort,
        max_concurrent_patches: int = _MAX_CONCURRENT_PATCHES,
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
        self._max_concurrent_patches = max_concurrent_patches
        # Cache wiring matches the Pod handler: when the watcher is
        # alive and the cache has entries for the request, the read
        # path skips the list call.  See
        # :meth:`K8sPodHandler._read_from_cache` for the
        # semantics.
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
        """Create a single Deployment with ``spec.replicas=N``.

        The Deployment is named ``orb-{request_id[:8]}``.  Pod names are
        assigned by the controller and are NOT known at acquire time —
        the strategy resolves them later via
        :meth:`check_hosts_status`.

        Returns a dict consumed by the strategy's ``acquire`` to build
        the :class:`Accepted` outcome.  ``resource_ids`` is the
        single-element list ``[deployment_name]`` (the workload
        identifier); ``machine_ids`` is empty at acquire time because
        the controller has not yet stamped pod names.
        ``provider_data`` carries the namespace, deployment name and
        the requested replica count so the release / status paths can
        recover context without re-querying.
        """
        namespace = self.resolve_namespace(template)
        replicas = max(int(request.requested_count), 1)
        deployment_name = make_deployment_name(str(request.request_id))

        self._logger.info(
            "Kubernetes deployment acquire: request_id=%s namespace=%s deployment=%s replicas=%s",
            request.request_id,
            namespace,
            deployment_name,
            replicas,
        )

        body = build_deployment_spec(
            template,
            request,
            deployment_name=deployment_name,
            namespace=namespace,
            replicas=replicas,
            provider_api=self.PROVIDER_API,
            config=self._config,
        )

        await asyncio.to_thread(
            self.with_retry,
            self.client.apps_v1.create_namespaced_deployment,
            namespace=namespace,
            body=body,
            operation_name="create_namespaced_deployment",
        )

        return {
            "resource_ids": [deployment_name],
            "machine_ids": [],
            "provider_data": {
                "namespace": namespace,
                "deployment_name": deployment_name,
                "replicas": replicas,
            },
        }

    # ------------------------------------------------------------------
    # check_hosts_status
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Return per-pod details + the Deployment-driven fulfilment verdict.

        Read path:

        1. Cache-first — if a :class:`PodStateCache` has been injected
           and the watcher reports alive, build the per-pod instance
           list from the cached states.  Stale entries are dropped
           transparently.
        2. Fallback — list the request's pods via
           ``list_namespaced_pod(label_selector=...)``.
        3. Always — read the Deployment object itself for
           ``availableReplicas`` / ``readyReplicas`` / ``conditions`` so
           the verdict reflects the controller's view (the pod list
           alone can lag behind a scale-down).
        """
        namespace = self._resolve_request_namespace(request)
        deployment_name = self._resolve_deployment_name(request)

        cached = self._read_from_cache(request)
        if cached is not None:
            # When the cache served the per-pod list, still rebase the
            # fulfilment verdict on the Deployment status because the
            # controller's view is authoritative for selective scale.
            cached_instances = self.apply_pod_timeouts(list(cached.instances))
            controller_view = self._read_deployment_status(namespace, deployment_name)
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
                "list_namespaced_pod failed for deployment request %s: %s",
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
        controller_view = self._read_deployment_status(namespace, deployment_name)
        fulfilment = self._compute_fulfilment(
            instances,
            request.requested_count,
            controller_view=controller_view,
        )
        return CheckHostsStatusResult(instances=instances, fulfilment=fulfilment)

    def _read_from_cache(self, request: Request) -> Optional[CheckHostsStatusResult]:
        """Cache-first read path; mirrors the Pod handler's logic."""
        cache = self._pod_state_cache
        if cache is None:
            return None
        if self._cache_alive is not None and not self._cache_alive():
            return None

        request_id = str(request.request_id)
        dropped = cache.mark_stale(request_id, self._stale_cache_timeout_seconds)
        if dropped:
            self._logger.debug(
                "Dropped %s stale pod cache entr%s for deployment request %s",
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

    def _read_deployment_status(self, namespace: str, deployment_name: str) -> dict[str, Any]:
        """Read ``availableReplicas``/``readyReplicas``/``conditions`` from the controller.

        Returned shape (all keys optional):

        * ``available_replicas`` — ``int`` or ``None``
        * ``ready_replicas``     — ``int`` or ``None``
        * ``updated_replicas``   — ``int`` or ``None``
        * ``replicas``           — ``int`` or ``None`` (controller spec)
        * ``conditions``         — list of ``{type, status, reason}`` dicts

        Missing fields default to ``None`` / empty so the caller can
        fall back to the pod-roll-up math without special-casing.
        """
        try:
            deployment = self.with_retry(
                self.client.apps_v1.read_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
                operation_name="read_namespaced_deployment",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                self._logger.debug(
                    "Deployment %s in %s not found — assuming pre-create or post-release",
                    deployment_name,
                    namespace,
                )
                return {}
            self._logger.warning(
                "read_namespaced_deployment failed (deployment=%s namespace=%s): %s",
                deployment_name,
                namespace,
                exc,
                exc_info=True,
            )
            return {}

        status = getattr(deployment, "status", None)
        spec = getattr(deployment, "spec", None)
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
            "available_replicas": getattr(status, "available_replicas", None),
            "ready_replicas": getattr(status, "ready_replicas", None),
            "updated_replicas": getattr(status, "updated_replicas", None),
            "replicas": getattr(spec, "replicas", None) if spec is not None else None,
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
        """Map ``pod.status.phase`` (+ readiness) to an ORB instance status."""
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
        """Roll up per-pod statuses + Deployment status into a verdict.

        When ``controller_view.ready_replicas`` is available, it
        overrides the per-pod ``running`` count for the
        ``fulfilled`` decision — the Deployment controller's view is
        authoritative across rolling updates and selective scale-downs.
        """
        controller_view = controller_view or {}
        ready_replicas = controller_view.get("ready_replicas")

        running_count = sum(1 for i in instances if i.get("status") == "running")
        pending_count = sum(1 for i in instances if i.get("status") in ("pending", "starting"))
        failed_count = sum(1 for i in instances if i.get("status") == "failed")

        # Prefer the controller's ready count when present.
        effective_ready = int(ready_replicas) if isinstance(ready_replicas, int) else running_count

        if effective_ready >= requested_count and failed_count == 0 and requested_count > 0:
            return ProviderFulfilment(
                state="fulfilled",
                message=f"Deployment ready: {effective_ready}/{requested_count} replicas",
                target_units=requested_count,
                fulfilled_units=effective_ready,
                running_count=effective_ready,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        if pending_count > 0:
            return ProviderFulfilment(
                state="in_progress",
                message=(
                    f"Deployment scaling up: {effective_ready}/{requested_count} ready, "
                    f"{pending_count} pending"
                ),
                target_units=requested_count,
                fulfilled_units=effective_ready,
                running_count=effective_ready,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        if failed_count > 0 and failed_count == len(instances) and len(instances) > 0:
            return ProviderFulfilment(
                state="failed",
                message=f"All {failed_count} replica pod(s) failed",
                target_units=requested_count,
                fulfilled_units=0,
                running_count=effective_ready,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        if effective_ready > 0:
            return ProviderFulfilment(
                state="partial",
                message=f"Deployment partial: {effective_ready}/{requested_count} ready",
                target_units=requested_count,
                fulfilled_units=effective_ready,
                running_count=effective_ready,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        return ProviderFulfilment(
            state="in_progress",
            message="Deployment starting",
            target_units=requested_count,
            fulfilled_units=0,
            running_count=effective_ready,
            pending_count=pending_count,
            failed_count=failed_count,
        )

    # ------------------------------------------------------------------
    # release_hosts
    # ------------------------------------------------------------------

    async def release_hosts(
        self,
        machine_ids: list[str],
        request: Request,
    ) -> None:
        """Selective or full release using pod-deletion-cost + replicas patch.

        Decision tree:

        * ``machine_ids`` empty → no-op (matches Pod handler semantics).
        * ``machine_ids`` covers every pod for the request → full release:
          patch ``spec.replicas: 0`` and delete the Deployment.
        * Otherwise → selective release:
          1. Annotate each victim pod with deletion cost ``-9999``.
          2. Patch ``spec.replicas`` to ``current - len(machine_ids)``.

        We never delete pods directly — the controller picks the
        annotated pods via deletion-cost ordering.  This preserves any
        PodDisruptionBudgets the operator may have configured.
        """
        if not machine_ids:
            self._logger.debug(
                "release_hosts called with no machine_ids for deployment request %s — no-op",
                request.request_id,
            )
            return

        namespace = self._resolve_request_namespace(request)
        deployment_name = self._resolve_deployment_name(request)

        deployment, current_replicas = await asyncio.to_thread(
            self._read_deployment_spec_replicas, namespace, deployment_name
        )
        if deployment is None:
            self._logger.warning(
                "Deployment %s not found in %s during release; assuming already gone",
                deployment_name,
                namespace,
            )
            return

        full_release = len(machine_ids) >= current_replicas
        self._logger.info(
            "Kubernetes deployment release: request_id=%s namespace=%s deployment=%s "
            "victims=%s current_replicas=%s full=%s",
            request.request_id,
            namespace,
            deployment_name,
            machine_ids,
            current_replicas,
            full_release,
        )

        if full_release:
            await self._patch_replicas(namespace, deployment_name, target=0)
            await self._delete_deployment(namespace, deployment_name)
            return

        # Step 1: annotate the victim pods.
        await self._annotate_victims(namespace=namespace, pod_names=machine_ids)
        # Step 2: scale down by the victim count.
        new_replicas = max(current_replicas - len(machine_ids), 0)
        await self._patch_replicas(namespace, deployment_name, target=new_replicas)

    async def _annotate_victims(self, *, namespace: str, pod_names: list[str]) -> None:
        """Patch each victim pod with the negative pod-deletion-cost annotation."""
        sem = asyncio.Semaphore(self._max_concurrent_patches)
        await asyncio.gather(
            *(
                self._annotate_one(sem=sem, namespace=namespace, pod_name=name)
                for name in pod_names
            ),
            return_exceptions=False,
        )

    async def _annotate_one(
        self,
        *,
        sem: asyncio.Semaphore,
        namespace: str,
        pod_name: str,
    ) -> None:
        """Patch a single victim pod's deletion-cost annotation.

        404s are best-effort: a pod that already evaporated is fine.
        """
        body = {
            "metadata": {
                "annotations": {
                    POD_DELETION_COST_ANNOTATION: VICTIM_DELETION_COST,
                }
            }
        }
        async with sem:
            try:
                await asyncio.to_thread(
                    self.client.core_v1.patch_namespaced_pod,
                    name=pod_name,
                    namespace=namespace,
                    body=body,
                )
                return
            except Exception as exc:
                if self.is_not_found(exc):
                    self._logger.debug(
                        "Victim pod %s in %s already gone (404) — annotation skipped",
                        pod_name,
                        namespace,
                    )
                    return
                self._logger.debug(
                    "Initial annotate failed for pod=%s in %s; retrying: %s",
                    pod_name,
                    namespace,
                    exc,
                )

            try:
                await asyncio.to_thread(
                    self.with_retry,
                    self.client.core_v1.patch_namespaced_pod,
                    name=pod_name,
                    namespace=namespace,
                    body=body,
                    operation_name="patch_namespaced_pod",
                )
            except Exception as exc:
                if self.is_not_found(exc):
                    return
                self._logger.warning(
                    "Failed to annotate victim pod=%s namespace=%s: %s",
                    pod_name,
                    namespace,
                    exc,
                )
                raise

    async def _patch_replicas(
        self,
        namespace: str,
        deployment_name: str,
        *,
        target: int,
    ) -> None:
        """Patch the Deployment's ``spec.replicas`` to ``target``."""
        body = {"spec": {"replicas": target}}
        try:
            await asyncio.to_thread(
                self.with_retry,
                self.client.apps_v1.patch_namespaced_deployment_scale,
                name=deployment_name,
                namespace=namespace,
                body=body,
                operation_name="patch_namespaced_deployment_scale",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                self._logger.debug(
                    "Deployment %s in %s gone during patch — treating as success",
                    deployment_name,
                    namespace,
                )
                return
            raise

    async def _delete_deployment(self, namespace: str, deployment_name: str) -> None:
        """Delete the Deployment after scaling to zero (full-release path)."""
        try:
            await asyncio.to_thread(
                self.client.apps_v1.delete_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
            )
            return
        except Exception as exc:
            if self.is_not_found(exc):
                self._logger.debug(
                    "Deployment %s in %s already gone (404) — delete is a no-op",
                    deployment_name,
                    namespace,
                )
                return
            self._logger.debug(
                "Initial delete failed for deployment=%s in %s; retrying: %s",
                deployment_name,
                namespace,
                exc,
            )

        try:
            await asyncio.to_thread(
                self.with_retry,
                self.client.apps_v1.delete_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
                operation_name="delete_namespaced_deployment",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                return
            self._logger.warning(
                "Failed to delete deployment=%s namespace=%s: %s",
                deployment_name,
                namespace,
                exc,
            )
            raise

    def _read_deployment_spec_replicas(
        self,
        namespace: str,
        deployment_name: str,
    ) -> tuple[Any, int]:
        """Return (deployment_object, current_spec_replicas).

        ``deployment_object`` is ``None`` when the Deployment is missing
        — the release path treats this as "already gone" and short-
        circuits.  ``current_spec_replicas`` defaults to ``0`` in that
        case so the caller's ``full_release`` decision still works.
        """
        try:
            deployment = self.with_retry(
                self.client.apps_v1.read_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
                operation_name="read_namespaced_deployment",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                return None, 0
            raise

        spec = getattr(deployment, "spec", None)
        replicas = getattr(spec, "replicas", None) if spec is not None else None
        return deployment, int(replicas) if isinstance(replicas, int) else 0

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

    def _resolve_deployment_name(self, request: Request) -> str:
        """Recover the deployment name created at acquire time.

        Persisted in ``request.provider_data["deployment_name"]`` by
        :meth:`acquire_hosts`; falls back to the deterministic
        :func:`make_deployment_name` when the field is missing so callers
        that operate on a freshly-loaded Request still resolve a
        sensible value.
        """
        provider_data = getattr(request, "provider_data", None) or {}
        if isinstance(provider_data, dict):
            name = provider_data.get("deployment_name")
            if isinstance(name, str) and name:
                return name
        return make_deployment_name(str(request.request_id))

    # ------------------------------------------------------------------
    # Examples
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Return one example template that submits as a ``Deployment``."""
        return [
            Template(
                template_id="k8s-deployment-example",
                name="Kubernetes Deployment example",
                description="Submit a Deployment-managed pod set via the kubernetes provider.",
                provider_type="k8s",
                provider_api="Deployment",
                image_id="busybox:latest",
                max_instances=3,
                provider_data={
                    "k8s": {
                        "container_image": "busybox:latest",
                        "resource_requests": {"cpu": "100m", "memory": "128Mi"},
                        "resource_limits": {"cpu": "500m", "memory": "256Mi"},
                        "command": ["sh", "-c", "sleep 3600"],
                    },
                },
            ),
        ]


__all__ = [
    "POD_DELETION_COST_ANNOTATION",
    "VICTIM_DELETION_COST",
    "K8sDeploymentHandler",
]

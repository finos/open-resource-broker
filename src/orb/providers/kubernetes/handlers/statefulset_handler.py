"""KubernetesStatefulSetHandler — ``apps/v1 StatefulSet`` provisioning handler.

Ordinal-based scale-down — StatefulSet controller semantics
===========================================================

A StatefulSet's controller assigns pod names deterministically using
ascending integer ordinals: ``<statefulset-name>-0``,
``<statefulset-name>-1``, ..., ``<statefulset-name>-(N-1)``.  Scaling a
StatefulSet down by ``k`` always removes the ``k`` highest-ordinal pods
(from ``N-1`` downwards) — this is a hard guarantee from the
StatefulSet controller and is the mechanism that makes StatefulSet pod
identity stable across rolling updates.

This rules out pod-deletion-cost-based selective termination (the
StatefulSet controller ignores the annotation for scale-down ordering;
unlike a Deployment, the controller cannot pick arbitrary pods to
remove).  When ORB's ``release_hosts(machine_ids=[...])`` is invoked
with victim names that include non-highest ordinals, the handler:

1. Computes the current top-of-stack ordinal range that *would* be
   removed by scaling down by ``len(machine_ids)``.
2. Logs a WARNING that the actual victims will differ from the caller's
   request (the controller will pick the highest ordinals).
3. Patches ``spec.replicas`` to ``current - len(machine_ids)`` and lets
   the controller do the eviction.

The full-release path (``machine_ids`` covers every pod for the request)
patches ``spec.replicas: 0`` directly and then deletes the StatefulSet.

The handler does **not** delete pods directly — it always leaves the
controller in charge of the actual termination so that any
``PodManagementPolicy`` / persistent-volume-claim retention behaviour
configured on the StatefulSet remains honoured.

Reference: Kubernetes documentation, "StatefulSet — Deployment and
scaling guarantees" — pods are created and terminated in strict
ascending / descending ordinal order
(https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#deployment-and-scaling-guarantees).

Phase scope
-----------

* ``acquire_hosts``    — creates one StatefulSet with
  ``spec.replicas=request.requested_count`` and a pod template inheriting
  the full ORB label set.
* ``check_hosts_status`` — lists pods via the request-id label selector
  (cache-first when a watcher is wired) and reads back
  ``readyReplicas`` / ``currentReplicas`` / ``conditions`` from the
  StatefulSet status for the rollup verdict.
* ``release_hosts``    — selective via ordinal-aware scale-down (with a
  WARNING when the requested victims are not the top-of-stack ordinals);
  full-release via replicas patch to zero + StatefulSet delete.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.handlers.base_handler import KubernetesHandlerBase
from orb.providers.kubernetes.infrastructure.kubernetes_client import KubernetesClient
from orb.providers.kubernetes.utilities.statefulset_spec import (
    build_statefulset_spec,
    make_statefulset_name,
    parse_statefulset_pod_ordinal,
)
from orb.providers.kubernetes.watch.pod_state_cache import PodState, PodStateCache


@injectable
class KubernetesStatefulSetHandler(KubernetesHandlerBase):
    """Handler for the ``KubernetesStatefulSet`` provider-API key.

    One ORB capacity unit equals one pod under a single StatefulSet
    (``apps/v1``).  Pod names are deterministic
    (``<statefulset-name>-<ordinal>``) and the StatefulSet controller
    always scales down from the highest ordinal; selective termination
    with arbitrary victim ordinals is therefore NOT supported by the
    controller, and this handler aligns with the controller's
    semantics — see the module docstring for the mechanism and caveat.
    """

    PROVIDER_API: str = "KubernetesStatefulSet"

    def __init__(
        self,
        kubernetes_client: KubernetesClient,
        config: KubernetesProviderConfig,
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
        # Cache wiring matches the Pod / Deployment handlers: when the
        # watcher is alive and the cache has entries for the request, the
        # read path skips the list call.  See
        # :meth:`KubernetesPodHandler._read_from_cache` for the semantics.
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
        """Create a single StatefulSet with ``spec.replicas=N``.

        The StatefulSet is named ``orb-{request_id[:8]}``.  Pods are
        stamped by the controller as ``orb-{request_id[:8]}-<ordinal>``
        (``ordinal`` is 0-indexed) and are NOT known at acquire time —
        the strategy resolves them later via
        :meth:`check_hosts_status`.

        Returns a dict consumed by the strategy's ``acquire`` to build
        the :class:`Accepted` outcome.  ``resource_ids`` is the
        single-element list ``[statefulset_name]``; ``machine_ids`` is
        empty at acquire time because the controller has not yet stamped
        pods.  ``provider_data`` carries the namespace, StatefulSet name
        and the requested replica count so the release / status paths can
        recover context without re-querying.
        """
        namespace = self.resolve_namespace(template)
        replicas = max(int(request.requested_count), 1)
        statefulset_name = make_statefulset_name(str(request.request_id))

        self._logger.info(
            "Kubernetes statefulset acquire: request_id=%s namespace=%s statefulset=%s replicas=%s",
            request.request_id,
            namespace,
            statefulset_name,
            replicas,
        )

        body = build_statefulset_spec(
            template,
            request,
            statefulset_name=statefulset_name,
            namespace=namespace,
            replicas=replicas,
            provider_api=self.PROVIDER_API,
            config=self._config,
        )

        await asyncio.to_thread(
            self.with_retry,
            self.client.apps_v1.create_namespaced_stateful_set,
            namespace=namespace,
            body=body,
            operation_name="create_namespaced_stateful_set",
        )

        return {
            "resource_ids": [statefulset_name],
            "machine_ids": [],
            "provider_data": {
                "namespace": namespace,
                "statefulset_name": statefulset_name,
                "replicas": replicas,
            },
        }

    # ------------------------------------------------------------------
    # check_hosts_status
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Return per-pod details + the StatefulSet-driven fulfilment verdict.

        Read path:

        1. Cache-first — if a :class:`PodStateCache` has been injected
           and the watcher reports alive, build the per-pod instance
           list from the cached states.  Stale entries are dropped
           transparently.
        2. Fallback — list the request's pods via
           ``list_namespaced_pod(label_selector=...)``.
        3. Always — read the StatefulSet object itself for
           ``readyReplicas`` / ``currentReplicas`` / ``conditions`` so
           the verdict reflects the controller's view (the pod list
           alone can lag behind a scale-down).
        """
        namespace = self._resolve_request_namespace(request)
        statefulset_name = self._resolve_statefulset_name(request)

        cached = self._read_from_cache(request)
        if cached is not None:
            # When the cache served the per-pod list, still rebase the
            # fulfilment verdict on the StatefulSet status because the
            # controller's view is authoritative for selective scale.
            cached_instances = self.apply_pod_timeouts(list(cached.instances))
            controller_view = self._read_statefulset_status(namespace, statefulset_name)
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
                "list_namespaced_pod failed for statefulset request %s: %s",
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
        controller_view = self._read_statefulset_status(namespace, statefulset_name)
        fulfilment = self._compute_fulfilment(
            instances,
            request.requested_count,
            controller_view=controller_view,
        )
        return CheckHostsStatusResult(instances=instances, fulfilment=fulfilment)

    def _read_from_cache(self, request: Request) -> Optional[CheckHostsStatusResult]:
        """Cache-first read path; mirrors the Pod / Deployment handlers' logic."""
        cache = self._pod_state_cache
        if cache is None:
            return None
        if self._cache_alive is not None and not self._cache_alive():
            return None

        request_id = str(request.request_id)
        dropped = cache.mark_stale(request_id, self._stale_cache_timeout_seconds)
        if dropped:
            self._logger.debug(
                "Dropped %s stale pod cache entr%s for statefulset request %s",
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

    def _read_statefulset_status(self, namespace: str, statefulset_name: str) -> dict[str, Any]:
        """Read controller view from ``apps/v1 StatefulSet.status``.

        Returned shape (all keys optional):

        * ``ready_replicas``   — ``int`` or ``None``
        * ``current_replicas`` — ``int`` or ``None``
        * ``updated_replicas`` — ``int`` or ``None``
        * ``replicas``         — ``int`` or ``None`` (controller spec)
        * ``conditions``       — list of ``{type, status, reason}`` dicts

        Missing fields default to ``None`` / empty so the caller can fall
        back to the pod-roll-up math without special-casing.
        """
        try:
            statefulset = self.with_retry(
                self.client.apps_v1.read_namespaced_stateful_set,
                name=statefulset_name,
                namespace=namespace,
                operation_name="read_namespaced_stateful_set",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                self._logger.debug(
                    "StatefulSet %s in %s not found — assuming pre-create or post-release",
                    statefulset_name,
                    namespace,
                )
                return {}
            self._logger.warning(
                "read_namespaced_stateful_set failed (statefulset=%s namespace=%s): %s",
                statefulset_name,
                namespace,
                exc,
                exc_info=True,
            )
            return {}

        status = getattr(statefulset, "status", None)
        spec = getattr(statefulset, "spec", None)
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
            "ready_replicas": getattr(status, "ready_replicas", None),
            "current_replicas": getattr(status, "current_replicas", None),
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
        """Roll up per-pod statuses + StatefulSet status into a verdict.

        When ``controller_view.ready_replicas`` is available, it
        overrides the per-pod ``running`` count for the
        ``fulfilled`` decision — the StatefulSet controller's view is
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
                message=f"StatefulSet ready: {effective_ready}/{requested_count} replicas",
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
                    f"StatefulSet scaling up: {effective_ready}/{requested_count} ready, "
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
                message=f"StatefulSet partial: {effective_ready}/{requested_count} ready",
                target_units=requested_count,
                fulfilled_units=effective_ready,
                running_count=effective_ready,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        return ProviderFulfilment(
            state="in_progress",
            message="StatefulSet starting",
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
        """Selective or full release using ordinal-aware scale-down.

        Decision tree:

        * ``machine_ids`` empty → no-op (matches Pod / Deployment handler
          semantics).
        * ``machine_ids`` covers every pod for the request → full release:
          patch ``spec.replicas: 0`` and delete the StatefulSet.
        * Otherwise → selective release:
          1. Inspect the victim ordinals.  The StatefulSet controller
             always evicts the highest-ordinal pods first, so if the
             caller's victims are not exactly the top-of-stack ordinals
             we log a WARNING that the actual victims will differ.
          2. Patch ``spec.replicas`` to
             ``current - len(machine_ids)``.  The controller picks the
             highest-ordinal pods to remove.

        We never delete pods directly — the controller picks the
        eviction order via ordinal semantics, preserving any per-pod
        PersistentVolumeClaim retention behaviour configured on the
        StatefulSet.

        **Caveat:** unlike the Deployment handler, this handler cannot
        target arbitrary victim pods.  Callers should treat
        ``machine_ids`` as a *count* of pods to release rather than a
        specific list.  This mirrors the StatefulSet controller's own
        scale-down semantics and is the only safe behaviour for a
        controller that owns stable pod identity.
        """
        if not machine_ids:
            self._logger.debug(
                "release_hosts called with no machine_ids for statefulset request %s — no-op",
                request.request_id,
            )
            return

        namespace = self._resolve_request_namespace(request)
        statefulset_name = self._resolve_statefulset_name(request)

        statefulset, current_replicas = await asyncio.to_thread(
            self._read_statefulset_spec_replicas, namespace, statefulset_name
        )
        if statefulset is None:
            self._logger.warning(
                "StatefulSet %s not found in %s during release; assuming already gone",
                statefulset_name,
                namespace,
            )
            return

        full_release = len(machine_ids) >= current_replicas
        self._logger.info(
            "Kubernetes statefulset release: request_id=%s namespace=%s statefulset=%s "
            "victims=%s current_replicas=%s full=%s",
            request.request_id,
            namespace,
            statefulset_name,
            machine_ids,
            current_replicas,
            full_release,
        )

        if full_release:
            await self._patch_replicas(namespace, statefulset_name, target=0)
            await self._delete_statefulset(namespace, statefulset_name)
            return

        # Selective release — warn if the victims are not the top-of-stack
        # ordinals.  The StatefulSet controller will always evict the
        # highest ordinals regardless of what the caller passed.
        self._warn_if_non_highest_ordinal_victims(
            statefulset_name=statefulset_name,
            current_replicas=current_replicas,
            requested_victims=machine_ids,
            request_id=str(request.request_id),
        )

        new_replicas = max(current_replicas - len(machine_ids), 0)
        await self._patch_replicas(namespace, statefulset_name, target=new_replicas)

    def _warn_if_non_highest_ordinal_victims(
        self,
        *,
        statefulset_name: str,
        current_replicas: int,
        requested_victims: list[str],
        request_id: str,
    ) -> None:
        """Emit a WARNING when the requested victims are not the top-of-stack ordinals.

        The StatefulSet controller will remove the ``len(requested_victims)``
        highest ordinals in ``[0, current_replicas - 1]``.  If the
        caller's ``requested_victims`` does not match that set we log a
        WARNING so operators can audit the discrepancy.  We still scale
        down — the caller asked to release *N* pods, and that is what
        happens; the only thing that changes is *which* ordinals.
        """
        # Expected actual victims = the top-of-stack ordinals.
        eviction_count = len(requested_victims)
        if eviction_count == 0 or current_replicas <= 0:
            return

        actual_ordinals = list(range(max(current_replicas - eviction_count, 0), current_replicas))
        actual_victim_names = {f"{statefulset_name}-{ordinal}" for ordinal in actual_ordinals}

        requested_set = set(requested_victims)
        if requested_set == actual_victim_names:
            return

        # Extract any ordinals that *can* be parsed from the requested
        # names so the WARNING is concrete; unparseable names are
        # reported verbatim.
        requested_ordinals: list[Optional[int]] = [
            parse_statefulset_pod_ordinal(name, statefulset_name) for name in requested_victims
        ]

        self._logger.warning(
            "StatefulSet selective release requested non-highest-ordinal victims for "
            "request %s (statefulset=%s, current_replicas=%s); the controller will "
            "evict the highest-ordinal pods instead.  requested_victims=%s "
            "requested_ordinals=%s actual_victims=%s",
            request_id,
            statefulset_name,
            current_replicas,
            requested_victims,
            requested_ordinals,
            sorted(actual_victim_names),
        )

    async def _patch_replicas(
        self,
        namespace: str,
        statefulset_name: str,
        *,
        target: int,
    ) -> None:
        """Patch the StatefulSet's ``spec.replicas`` to ``target``."""
        body = {"spec": {"replicas": target}}
        try:
            await asyncio.to_thread(
                self.with_retry,
                self.client.apps_v1.patch_namespaced_stateful_set_scale,
                name=statefulset_name,
                namespace=namespace,
                body=body,
                operation_name="patch_namespaced_stateful_set_scale",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                self._logger.debug(
                    "StatefulSet %s in %s gone during patch — treating as success",
                    statefulset_name,
                    namespace,
                )
                return
            raise

    async def _delete_statefulset(self, namespace: str, statefulset_name: str) -> None:
        """Delete the StatefulSet after scaling to zero (full-release path)."""
        try:
            await asyncio.to_thread(
                self.client.apps_v1.delete_namespaced_stateful_set,
                name=statefulset_name,
                namespace=namespace,
            )
            return
        except Exception as exc:
            if self.is_not_found(exc):
                self._logger.debug(
                    "StatefulSet %s in %s already gone (404) — delete is a no-op",
                    statefulset_name,
                    namespace,
                )
                return
            self._logger.debug(
                "Initial delete failed for statefulset=%s in %s; retrying: %s",
                statefulset_name,
                namespace,
                exc,
            )

        try:
            await asyncio.to_thread(
                self.with_retry,
                self.client.apps_v1.delete_namespaced_stateful_set,
                name=statefulset_name,
                namespace=namespace,
                operation_name="delete_namespaced_stateful_set",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                return
            self._logger.warning(
                "Failed to delete statefulset=%s namespace=%s: %s",
                statefulset_name,
                namespace,
                exc,
            )
            raise

    def _read_statefulset_spec_replicas(
        self,
        namespace: str,
        statefulset_name: str,
    ) -> tuple[Any, int]:
        """Return ``(statefulset_object, current_spec_replicas)``.

        ``statefulset_object`` is ``None`` when the StatefulSet is
        missing — the release path treats this as "already gone" and
        short-circuits.  ``current_spec_replicas`` defaults to ``0`` in
        that case so the caller's ``full_release`` decision still works.
        """
        try:
            statefulset = self.with_retry(
                self.client.apps_v1.read_namespaced_stateful_set,
                name=statefulset_name,
                namespace=namespace,
                operation_name="read_namespaced_stateful_set",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                return None, 0
            raise

        spec = getattr(statefulset, "spec", None)
        replicas = getattr(spec, "replicas", None) if spec is not None else None
        return statefulset, int(replicas) if isinstance(replicas, int) else 0

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

    def _resolve_statefulset_name(self, request: Request) -> str:
        """Recover the StatefulSet name created at acquire time.

        Persisted in ``request.provider_data["statefulset_name"]`` by
        :meth:`acquire_hosts`; falls back to the deterministic
        :func:`make_statefulset_name` when the field is missing so callers
        that operate on a freshly-loaded Request still resolve a
        sensible value.
        """
        provider_data = getattr(request, "provider_data", None) or {}
        if isinstance(provider_data, dict):
            name = provider_data.get("statefulset_name")
            if isinstance(name, str) and name:
                return name
        return make_statefulset_name(str(request.request_id))

    # ------------------------------------------------------------------
    # Examples
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Return one example template that submits as a ``KubernetesStatefulSet``."""
        return [
            Template(
                template_id="kubernetes-statefulset-example",
                name="Kubernetes StatefulSet example",
                description="Submit a StatefulSet-managed pod set via the kubernetes provider.",
                provider_type="kubernetes",
                provider_api="KubernetesStatefulSet",
                image_id="busybox:latest",
                max_instances=3,
                provider_data={
                    "kubernetes": {
                        "container_image": "busybox:latest",
                        "resource_requests": {"cpu": "100m", "memory": "128Mi"},
                        "resource_limits": {"cpu": "500m", "memory": "256Mi"},
                        "command": ["sh", "-c", "sleep 3600"],
                    },
                },
            ),
        ]


__all__ = [
    "KubernetesStatefulSetHandler",
]

"""K8sStatefulSetHandler — ``apps/v1 StatefulSet`` provisioning handler.

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
2. Raises :class:`K8sError` when the caller-supplied victims are not
   exactly that top-of-stack set — silently evicting different pods
   would cause data loss.
3. Otherwise patches ``spec.replicas`` to
   ``current - len(machine_ids)`` and lets the controller do the
   eviction.

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

Module split
------------

* ``acquire_hosts``    — creates one StatefulSet with
  ``spec.replicas=request.requested_count`` and a pod template inheriting
  the full ORB label set.
* ``check_hosts_status`` — delegated to
  :class:`orb.providers.k8s.handlers.statefulset_status.StatefulSetStatusResolver`
  which lists pods via the request-id label selector (cache-first when
  a watcher is wired) and reads back the controller view from the
  StatefulSet status.
* ``release_hosts``    — selective via ordinal-aware scale-down; the
  handler refuses the request when the caller-supplied victims are not
  the top-of-stack ordinals.  Full-release via replicas patch to zero
  + StatefulSet delete.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.di.injectable import injectable
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.exceptions.k8s_errors import K8sError
from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.infrastructure.handlers.statefulset_status import StatefulSetStatusResolver
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.utilities.statefulset_spec import (
    build_statefulset_spec,
    make_statefulset_name,
    parse_statefulset_pod_ordinal,
)
from orb.providers.k8s.watch.pod_state_cache import PodStateCache


@injectable
class K8sStatefulSetHandler(K8sHandlerBase):
    """Handler for the ``StatefulSet`` provider-API key.

    One ORB capacity unit equals one pod under a single StatefulSet
    (``apps/v1``).  Pod names are deterministic
    (``<statefulset-name>-<ordinal>``) and the StatefulSet controller
    always scales down from the highest ordinal; selective termination
    with arbitrary victim ordinals is therefore NOT supported by the
    controller, and this handler aligns with the controller's
    semantics — see the module docstring for the mechanism and caveat.
    """

    PROVIDER_API: str = "StatefulSet"

    def __init__(
        self,
        kubernetes_client: K8sClient,
        config: K8sProviderConfig,
        logger: LoggingPort,
        *,
        pod_state_cache: Optional[PodStateCache] = None,
        cache_alive: Optional[Callable[[], bool]] = None,
        stale_cache_timeout_seconds: Optional[float] = None,
        native_spec_service: Optional[Any] = None,
        node_state_cache: Optional[Any] = None,
        metrics: Optional[Any] = None,
    ) -> None:
        super().__init__(
            kubernetes_client=kubernetes_client,
            config=config,
            logger=logger,
            pod_state_cache=pod_state_cache,
            cache_alive=cache_alive,
            stale_cache_timeout_seconds=stale_cache_timeout_seconds,
            native_spec_service=native_spec_service,
            node_state_cache=node_state_cache,
            metrics=metrics,
        )
        self._status_resolver = StatefulSetStatusResolver(self)

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

        self._record_acquire(namespace=namespace, spec_kind=self.PROVIDER_API)
        self._logger.info(
            "Kubernetes statefulset acquire: request_id=%s namespace=%s statefulset=%s replicas=%s",
            request.request_id,
            namespace,
            statefulset_name,
            replicas,
        )

        native_body = (
            self._native_spec_service.process_statefulset_spec(
                template, request, namespace=namespace
            )
            if self._native_spec_service is not None
            else None
        )
        if native_body is not None:
            body: Any = self._stamp_native_workload_body(
                native_body,
                workload_name=statefulset_name,
                namespace=namespace,
                replicas=replicas,
                request=request,
            )
        else:
            body = build_statefulset_spec(
                template,
                request,
                statefulset_name=statefulset_name,
                namespace=namespace,
                replicas=replicas,
                provider_api=self.PROVIDER_API,
                config=self._config,
            )

        self._audit_spec_body(body)

        await asyncio.to_thread(
            self.with_retry,
            self.client.apps_v1.create_namespaced_stateful_set,
            namespace=namespace,
            body=body,
            operation_name="create_namespaced_stateful_set",
        )

        return {
            "success": True,
            "resource_ids": [statefulset_name],
            "machine_ids": [],
            "provider_data": {
                "request_id": str(request.request_id),
                "namespace": namespace,
                "statefulset_name": statefulset_name,
                "replicas": replicas,
            },
        }

    # ------------------------------------------------------------------
    # check_hosts_status — delegated to StatefulSetStatusResolver
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Delegate the status read path to :class:`StatefulSetStatusResolver`."""
        return self._status_resolver.check_hosts_status(request)

    # ------------------------------------------------------------------
    # release_hosts
    # ------------------------------------------------------------------

    async def release_hosts(
        self,
        machine_ids: list[str],
        provider_data: dict[str, Any],
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

        Args:
            machine_ids: Pod names the caller wants to release.
            provider_data: The ``provider_data`` dict stamped onto the
                Request aggregate at acquire time.  Carries ``namespace``
                and ``statefulset_name`` (falls back to deterministic
                defaults when absent).
        """
        request_id = provider_data.get("request_id", "unknown")
        if not machine_ids:
            self._logger.debug(
                "release_hosts called with no machine_ids for statefulset request %s — no-op",
                request_id,
            )
            return

        namespace = self._resolve_namespace_from_provider_data(provider_data)
        statefulset_name = self._resolve_statefulset_name_from_provider_data(provider_data)
        self._record_release(namespace=namespace, spec_kind=self.PROVIDER_API)

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
            request_id,
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

        # Selective release — REFUSE when caller-supplied victims are not
        # the top-of-stack ordinals.  Silently evicting different pods
        # would cause data loss; the caller can either supply the correct
        # top-of-stack victims or trigger a full release explicitly.
        self._reject_non_highest_ordinal_victims(
            statefulset_name=statefulset_name,
            current_replicas=current_replicas,
            requested_victims=machine_ids,
            request_id=request_id,
        )

        new_replicas = max(current_replicas - len(machine_ids), 0)
        await self._patch_replicas(namespace, statefulset_name, target=new_replicas)

    def _reject_non_highest_ordinal_victims(
        self,
        *,
        statefulset_name: str,
        current_replicas: int,
        requested_victims: list[str],
        request_id: str,
    ) -> None:
        """Raise :class:`K8sError` when the requested victims are not top-of-stack."""
        eviction_count = len(requested_victims)
        if eviction_count == 0 or current_replicas <= 0:
            return

        actual_ordinals = list(range(max(current_replicas - eviction_count, 0), current_replicas))
        actual_victim_names = {f"{statefulset_name}-{ordinal}" for ordinal in actual_ordinals}

        requested_set = set(requested_victims)
        if requested_set == actual_victim_names:
            return

        requested_ordinals: list[Optional[int]] = [
            parse_statefulset_pod_ordinal(name, statefulset_name) for name in requested_victims
        ]

        raise K8sError(
            "StatefulSet selective release refused for "
            f"request {request_id} (statefulset={statefulset_name}, "
            f"current_replicas={current_replicas}): the controller only supports "
            "scale-down from the top of the ordinal stack, so the caller-supplied "
            "victims cannot be honoured.  Either supply the top-of-stack pod names "
            f"({sorted(actual_victim_names)}) or release every pod for the request. "
            f"requested_victims={requested_victims} requested_ordinals={requested_ordinals}"
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

    def _resolve_statefulset_name_from_provider_data(self, provider_data: dict[str, Any]) -> str:
        """Recover the StatefulSet name from a ``provider_data`` dict.

        Reads the ``statefulset_name`` key written by ``acquire_hosts``; falls
        back to the deterministic :func:`make_statefulset_name` using the
        ``request_id`` key when the field is missing.
        """
        name = provider_data.get("statefulset_name")
        if isinstance(name, str) and name:
            return name
        return make_statefulset_name(str(provider_data.get("request_id", "unknown")))

    def _resolve_statefulset_name(self, request: Request) -> str:
        """Thin wrapper for status resolvers that hold the full Request aggregate."""
        provider_data = getattr(request, "provider_data", None) or {}
        pd = provider_data if isinstance(provider_data, dict) else {}
        name = pd.get("statefulset_name")
        if isinstance(name, str) and name:
            return name
        return make_statefulset_name(str(request.request_id))

    # ------------------------------------------------------------------
    # Examples
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Return one example template that submits as a ``StatefulSet``."""
        from orb.providers.k8s.domain.template.k8s_template import (
            K8sResourceQuantities,
            K8sTemplate,
        )

        return [
            K8sTemplate(
                template_id="k8s-statefulset-example",
                name="Kubernetes StatefulSet example",
                description="Submit a StatefulSet-managed pod set via the kubernetes provider.",
                provider_api="StatefulSet",
                image_id="busybox:latest",
                max_instances=3,
                resource_requests=K8sResourceQuantities(cpu="100m", memory="128Mi"),
                resource_limits=K8sResourceQuantities(cpu="500m", memory="256Mi"),
                command=["sh", "-c", "sleep 3600"],
            ),
        ]


__all__ = [
    "K8sStatefulSetHandler",
]

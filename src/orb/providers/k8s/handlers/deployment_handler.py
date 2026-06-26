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

Module split
------------

* ``acquire_hosts``    — creates one Deployment with
  ``spec.replicas=request.requested_count`` and a pod template inheriting
  the full ORB label set.
* ``check_hosts_status`` — delegated to
  :class:`orb.providers.k8s.handlers.deployment_status.DeploymentStatusResolver`
  which lists pods via the request-id label selector (cache-first when
  a watcher is wired) and reads back the controller view from the
  Deployment status.
* ``release_hosts``    — selective via pod-deletion-cost + replicas
  patch; full-release via replicas patch to zero + Deployment delete.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.handlers.deployment_status import DeploymentStatusResolver
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.utilities.deployment_spec import (
    build_deployment_spec,
    make_deployment_name,
)
from orb.providers.k8s.watch.pod_state_cache import PodStateCache

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
        native_spec_service: Optional[Any] = None,
    ) -> None:
        super().__init__(
            kubernetes_client=kubernetes_client,
            config=config,
            logger=logger,
            pod_state_cache=pod_state_cache,
            cache_alive=cache_alive,
            stale_cache_timeout_seconds=stale_cache_timeout_seconds,
            native_spec_service=native_spec_service,
        )
        self._max_concurrent_patches = max_concurrent_patches
        self._status_resolver = DeploymentStatusResolver(self)

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

        native_body = (
            self._native_spec_service.process_deployment_spec(
                template, request, namespace=namespace
            )
            if self._native_spec_service is not None
            else None
        )
        if native_body is not None:
            # Operator supplied a full Deployment spec — stamp identity
            # and submit the rendered dict directly.
            body: Any = self._stamp_native_workload_body(
                native_body,
                workload_name=deployment_name,
                namespace=namespace,
                replicas=replicas,
                request=request,
            )
        else:
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
    # check_hosts_status — delegated to DeploymentStatusResolver
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Delegate the status read path to :class:`DeploymentStatusResolver`."""
        return self._status_resolver.check_hosts_status(request)

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
        """Patch each victim pod with the negative pod-deletion-cost annotation.

        Uses ``return_exceptions=True`` so that a single transient annotation
        failure does not abort the remaining victims.  A warning is logged for
        each individual failure so operators can identify flaky pods.

        If more than half of the victims could not be annotated the release is
        aborted — the controller would pick arbitrary pods instead of the
        intended victims, which could silently violate the caller's selection.
        When the majority succeed ORB proceeds with the replicas patch: the
        controller still preferentially terminates the annotated pods, and the
        unannotated ones will be terminated last (lowest-cost-first ordering
        remains intact for the annotated subset).
        """
        sem = asyncio.Semaphore(self._max_concurrent_patches)
        results = await asyncio.gather(
            *(
                self._annotate_one(sem=sem, namespace=namespace, pod_name=name)
                for name in pod_names
            ),
            return_exceptions=True,
        )

        failed: list[tuple[str, BaseException]] = [
            (name, exc) for name, exc in zip(pod_names, results) if isinstance(exc, BaseException)
        ]

        if not failed:
            return

        for pod_name, exc in failed:
            self._logger.warning(
                "Victim annotation failed for pod=%s namespace=%s: %s",
                pod_name,
                namespace,
                exc,
            )

        failure_rate = len(failed) / len(pod_names)
        if failure_rate > 0.5:
            raise RuntimeError(
                f"Aborted selective release: {len(failed)}/{len(pod_names)} victim annotations "
                f"failed in namespace={namespace!r} — the controller would not honour "
                f"deletion-cost ordering. Transient errors: "
                + ", ".join(f"{name}: {exc}" for name, exc in failed)
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
        from orb.providers.k8s.domain.template.k8s_template import (  # noqa: PLC0415
            K8sResourceQuantities,
            K8sTemplate,
        )

        return [
            K8sTemplate(
                template_id="k8s-deployment-example",
                name="Kubernetes Deployment example",
                description="Submit a Deployment-managed pod set via the kubernetes provider.",
                provider_api="Deployment",
                image_id="busybox:latest",
                max_instances=3,
                resource_requests=K8sResourceQuantities(cpu="100m", memory="128Mi"),
                resource_limits=K8sResourceQuantities(cpu="500m", memory="256Mi"),
                command=["sh", "-c", "sleep 3600"],
            ),
        ]


__all__ = [
    "POD_DELETION_COST_ANNOTATION",
    "VICTIM_DELETION_COST",
    "K8sDeploymentHandler",
]

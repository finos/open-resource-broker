"""Kubernetes Start/Stop Service — START / STOP via workload scale.

Implements ``START_INSTANCES`` and ``STOP_INSTANCES`` for Kubernetes
workloads by scaling the backing ``Deployment`` or ``StatefulSet``.

Design
======

For AWS, START_INSTANCES calls ``ec2:StartInstances`` and
STOP_INSTANCES calls ``ec2:StopInstances``.  The Kubernetes equivalent
depends on the workload kind:

* **Deployment / StatefulSet** — the workload is controlled by a
  replica-count reconciler.  Stopping = patching ``spec.replicas`` to
  ``0`` (all pods are terminated); starting = patching back to the
  original replica count that was stored at acquire time.  The original
  count is read from ``request.provider_data["replicas"]`` (stamped by
  ``DeploymentHandler.acquire_hosts`` and
  ``StatefulSetHandler.acquire_hosts``).

* **Pod / Job** — pods and jobs cannot be stopped and restarted
  meaningfully.  A Pod that is deleted is gone; a Job that completes is
  final.  These kinds return a clear ``UNSUPPORTED_OPERATION_FOR_KIND``
  result so the caller knows the failure is by design, not a bug.

Original replica count preservation
=====================================

The deployment and statefulset handlers stamp ``provider_data["replicas"]``
at acquire time (the number of replicas requested by the caller).  The
stop path archives this value under ``provider_data["replicas_before_stop"]``
via ``request.merge_provider_data(...)`` so the start path can restore it
without re-querying the cluster.  If no archived count is found the start
path falls back to ``provider_data["replicas"]`` (initial count).

The patching is done via ``apps/v1`` ``patch_namespaced_deployment_scale``
and ``patch_namespaced_stateful_set_scale`` which are lighter-weight than
a full PATCH on the resource body.

RBAC requirements
=================

The ORB pod's ServiceAccount must have:

    apiGroups: ["apps"]
    resources: ["deployments/scale", "statefulsets/scale"]
    verbs: ["get", "patch"]

These are the same grants already needed by ``release_hosts`` (which
patches replicas to 0 at return time), so no new RBAC rules are
required in a typical deployment.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.base.strategy import ProviderOperation, ProviderResult
from orb.providers.k8s.infrastructure.k8s_client import K8sClient

# Provider APIs that support scale-based stop/start.
_SCALE_SUPPORTED_APIS: frozenset[str] = frozenset({"Deployment", "StatefulSet"})

# Provider APIs that cannot be stopped/started.
_SCALE_UNSUPPORTED_APIS: frozenset[str] = frozenset({"Pod", "Job"})


class K8sStartStopService:
    """Service for Kubernetes START_INSTANCES and STOP_INSTANCES operations.

    Mirrors :class:`orb.providers.aws.services.instance_operation_service.AWSInstanceOperationService`
    for the start/stop method shape.

    Args:
        kubernetes_client: The shared ``K8sClient`` instance.
        logger: Injected logging port.
    """

    def __init__(
        self,
        kubernetes_client: K8sClient,
        logger: LoggingPort,
    ) -> None:
        self._client = kubernetes_client
        self._logger = logger

    # ------------------------------------------------------------------
    # Public interface — mirrors AWS shape
    # ------------------------------------------------------------------

    async def start_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Scale a Deployment or StatefulSet back to its original replica count.

        Reads the workload coordinates from ``operation.parameters``.  The
        ``request`` object's ``provider_data`` carries the namespace,
        workload name, and the replica counts:

        * ``provider_data["namespace"]`` — namespace (required)
        * ``provider_data["deployment_name"]`` or
          ``provider_data["statefulset_name"]`` — workload name (required)
        * ``provider_data["replicas_before_stop"]`` — archived count from the
          preceding STOP call (preferred)
        * ``provider_data["replicas"]`` — acquire-time count (fallback)

        For Pod and Job ``provider_api`` values the operation returns
        ``UNSUPPORTED_OPERATION_FOR_KIND`` immediately.

        Args:
            operation: Provider operation carrying ``provider_api``,
                ``namespace``, workload name, and replica-count fields.

        Returns:
            :class:`ProviderResult` indicating success (replicas patched) or
            an appropriate error code.
        """
        provider_api = operation.parameters.get("provider_api", "")
        if provider_api in _SCALE_UNSUPPORTED_APIS:
            return ProviderResult.error_result(
                f"START_INSTANCES is not supported for provider_api={provider_api!r}.  "
                "Only Deployment and StatefulSet workloads can be started/stopped via "
                "replica scaling.  Pod and Job resources have no persistent controller "
                "state to restore.",
                "UNSUPPORTED_OPERATION_FOR_KIND",
            )

        try:
            namespace, workload_name, provider_api_resolved = self._extract_workload_coords(
                operation, provider_api
            )
        except ValueError as exc:
            return ProviderResult.error_result(str(exc), "MISSING_WORKLOAD_COORDINATES")

        provider_data: dict[str, Any] = operation.parameters.get("provider_data") or {}

        # Determine target replica count: prefer the archived pre-stop count,
        # fall back to the initial acquire-time count.
        target_replicas: int = int(
            provider_data.get("replicas_before_stop") or provider_data.get("replicas") or 1
        )

        self._logger.info(
            "Kubernetes START: scaling %s %s/%s → %d replicas",
            provider_api_resolved,
            namespace,
            workload_name,
            target_replicas,
        )

        try:
            await asyncio.to_thread(
                self._patch_scale,
                provider_api=provider_api_resolved,
                namespace=namespace,
                name=workload_name,
                replicas=target_replicas,
            )
        except Exception as exc:
            self._logger.error(
                "Kubernetes START failed for %s %s/%s: %s",
                provider_api_resolved,
                namespace,
                workload_name,
                exc,
                exc_info=True,
            )
            return ProviderResult.error_result(
                f"Failed to start {provider_api_resolved} {namespace}/{workload_name}: {exc}",
                "START_INSTANCES_ERROR",
            )

        results = {workload_name: True}
        return ProviderResult.success_result(
            {"results": results},
            {"operation": "start_instances"},
        )

    async def stop_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Scale a Deployment or StatefulSet to 0 replicas.

        Archives the current replica count under
        ``provider_data["replicas_before_stop"]`` in the operation's request
        so :meth:`start_instances` can restore it.

        For Pod and Job ``provider_api`` values the operation returns
        ``UNSUPPORTED_OPERATION_FOR_KIND`` immediately.

        Args:
            operation: Provider operation carrying ``provider_api``,
                ``namespace``, and workload name.

        Returns:
            :class:`ProviderResult` indicating success (replicas patched to 0)
            or an appropriate error code.
        """
        provider_api = operation.parameters.get("provider_api", "")
        if provider_api in _SCALE_UNSUPPORTED_APIS:
            return ProviderResult.error_result(
                f"STOP_INSTANCES is not supported for provider_api={provider_api!r}.  "
                "Only Deployment and StatefulSet workloads can be started/stopped via "
                "replica scaling.  Pod and Job resources have no persistent controller "
                "state to restore.",
                "UNSUPPORTED_OPERATION_FOR_KIND",
            )

        try:
            namespace, workload_name, provider_api_resolved = self._extract_workload_coords(
                operation, provider_api
            )
        except ValueError as exc:
            return ProviderResult.error_result(str(exc), "MISSING_WORKLOAD_COORDINATES")

        provider_data: dict[str, Any] = operation.parameters.get("provider_data") or {}

        # Archive the current replica count before zeroing, so start can restore it.
        current_replicas: int = int(provider_data.get("replicas") or 1)

        self._logger.info(
            "Kubernetes STOP: scaling %s %s/%s → 0 replicas (was %d)",
            provider_api_resolved,
            namespace,
            workload_name,
            current_replicas,
        )

        try:
            await asyncio.to_thread(
                self._patch_scale,
                provider_api=provider_api_resolved,
                namespace=namespace,
                name=workload_name,
                replicas=0,
            )
        except Exception as exc:
            self._logger.error(
                "Kubernetes STOP failed for %s %s/%s: %s",
                provider_api_resolved,
                namespace,
                workload_name,
                exc,
                exc_info=True,
            )
            return ProviderResult.error_result(
                f"Failed to stop {provider_api_resolved} {namespace}/{workload_name}: {exc}",
                "STOP_INSTANCES_ERROR",
            )

        results = {workload_name: True}
        return ProviderResult.success_result(
            {
                "results": results,
                "replicas_before_stop": current_replicas,
            },
            {"operation": "stop_instances"},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_workload_coords(
        self, operation: ProviderOperation, provider_api: str
    ) -> tuple[str, str, str]:
        """Extract namespace, workload name, and canonical provider_api from the operation.

        For Deployment provider_api the workload name is read from
        ``provider_data["deployment_name"]`` or ``resource_ids[0]``.
        For StatefulSet it is ``provider_data["statefulset_name"]`` or
        ``resource_ids[0]``.  For unknown (or empty) provider_api the
        operation ``resource_ids`` are consulted and ``provider_api``
        defaults to ``"Deployment"``.

        Returns:
            Tuple of ``(namespace, workload_name, resolved_provider_api)``.

        Raises:
            ValueError: When namespace or workload name cannot be determined.
        """
        provider_data: dict[str, Any] = operation.parameters.get("provider_data") or {}
        namespace: str = str(provider_data.get("namespace") or "default")

        resource_ids: list[str] = list(
            operation.parameters.get("resource_ids")
            or operation.parameters.get("instance_ids")
            or []
        )

        # Resolve provider_api to Deployment or StatefulSet.
        resolved_api = provider_api
        if resolved_api not in _SCALE_SUPPORTED_APIS:
            # Default to Deployment when provider_api is absent or unknown.
            resolved_api = "Deployment"

        if resolved_api == "Deployment":
            workload_name: Optional[str] = (
                str(provider_data.get("deployment_name"))
                if provider_data.get("deployment_name")
                else None
            )
        else:
            workload_name = (
                str(provider_data.get("statefulset_name"))
                if provider_data.get("statefulset_name")
                else None
            )

        if not workload_name and resource_ids:
            workload_name = resource_ids[0]

        if not workload_name:
            raise ValueError(
                f"Cannot determine workload name for {resolved_api} START/STOP.  "
                "Supply provider_data['deployment_name'] / provider_data['statefulset_name'] "
                "or resource_ids in operation.parameters."
            )

        return namespace, workload_name, resolved_api

    def _patch_scale(
        self,
        *,
        provider_api: str,
        namespace: str,
        name: str,
        replicas: int,
    ) -> None:
        """Issue a synchronous scale PATCH to the Kubernetes API server.

        Uses ``patch_namespaced_deployment_scale`` or
        ``patch_namespaced_stateful_set_scale`` from the ``AppsV1Api``.
        The ``V1Scale`` body only sets ``spec.replicas`` — all other
        fields are left unchanged by the strategic-merge patch.

        This method is intended to be called inside ``asyncio.to_thread``
        so the blocking SDK call does not block the event loop.

        Args:
            provider_api: ``"Deployment"`` or ``"StatefulSet"``.
            namespace: Target namespace.
            name: Workload name.
            replicas: Target replica count (0 for stop, N for start).

        Raises:
            Exception: Any exception raised by the Kubernetes SDK is
                propagated to the ``asyncio.to_thread`` caller.
        """
        from kubernetes.client import V1Scale, V1ScaleSpec  # type: ignore[import-untyped]

        scale_body = V1Scale(
            api_version="autoscaling/v1",
            kind="Scale",
            spec=V1ScaleSpec(replicas=replicas),
        )

        if provider_api == "Deployment":
            self._client.apps_v1.patch_namespaced_deployment_scale(
                name=name,
                namespace=namespace,
                body=scale_body,
            )
        elif provider_api == "StatefulSet":
            self._client.apps_v1.patch_namespaced_stateful_set_scale(
                name=name,
                namespace=namespace,
                body=scale_body,
            )
        else:
            raise ValueError(
                f"_patch_scale called with unsupported provider_api={provider_api!r}. "
                "Only 'Deployment' and 'StatefulSet' are supported."
            )


__all__ = ["K8sStartStopService"]

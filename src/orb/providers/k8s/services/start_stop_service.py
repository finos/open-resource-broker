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
  original replica count that was stored at acquire time.

* **Pod / Job** — pods and jobs cannot be stopped and restarted
  meaningfully.  These kinds return a clear ``UNSUPPORTED_OPERATION_FOR_KIND``
  result so the caller knows the failure is by design, not a bug.

Workload coordinate threading
==============================

The orchestrators pass per-machine coordinates in
``operation.parameters["machine_coordinates"]`` — a dict keyed by
machine_id.  Each entry holds:

  ``provider_data`` — the machine's stored provider_data dict
    (contains ``namespace``, ``deployment_name``/``statefulset_name``,
    ``replicas``, ``replicas_before_stop`` etc.)
  ``provider_api``  — the originating provider_api ("Deployment", "StatefulSet", ...)
  ``resource_id``   — the acquire-time controller name (fallback name source)

When ``machine_coordinates`` is absent the service falls back to the
top-level ``provider_data`` / ``provider_api`` keys for backwards
compatibility with non-orchestrator callers (e.g. direct unit tests).

STOP returns ``replicas_before_stop_per_machine`` in its result data so
the orchestrator can persist the count per machine, enabling START to
restore the correct value even after a manual scale event between the
two operations.

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
        """Scale Deployment or StatefulSet workloads back to their original replica count.

        When ``operation.parameters["machine_coordinates"]`` is present (populated
        by :class:`StartMachinesOrchestrator`) each machine's own ``provider_data``
        is used to resolve namespace, workload name, provider_api, and the
        archived ``replicas_before_stop`` count.  When absent the method falls
        back to the top-level ``provider_data`` / ``provider_api`` keys for
        backwards-compatible single-machine callers.

        For Pod and Job ``provider_api`` values the call returns
        ``UNSUPPORTED_OPERATION_FOR_KIND`` immediately.

        Args:
            operation: Provider operation carrying per-machine coordinates
                in ``parameters["machine_coordinates"]`` or top-level
                ``provider_data`` / ``provider_api`` keys.

        Returns:
            :class:`ProviderResult` indicating success or an appropriate error code.
        """
        machine_coordinates: dict[str, Any] = operation.parameters.get("machine_coordinates") or {}

        # Legacy single-machine path: no machine_coordinates key.
        if not machine_coordinates:
            return await self._start_single_legacy(operation)

        # Per-machine path from the orchestrator.
        results: dict[str, bool] = {}

        for machine_id, coords in machine_coordinates.items():
            provider_data: dict[str, Any] = coords.get("provider_data") or {}
            provider_api: str = coords.get("provider_api") or ""

            if provider_api in _SCALE_UNSUPPORTED_APIS:
                self._logger.warning(
                    "Kubernetes START: skipping machine %s — provider_api=%r cannot be "
                    "started via replica scaling (Pod/Job)",
                    machine_id,
                    provider_api,
                )
                results[machine_id] = False
                continue

            try:
                namespace, workload_name, resolved_api = self._extract_workload_coords_from_data(
                    provider_data=provider_data,
                    provider_api=provider_api,
                    resource_id=coords.get("resource_id") or "",
                )
            except ValueError as exc:
                self._logger.error(
                    "Kubernetes START: cannot resolve workload for machine %s: %s",
                    machine_id,
                    exc,
                )
                results[machine_id] = False
                continue

            # Prefer the archived pre-stop count; fall back to acquire-time count.
            target_replicas: int = int(
                provider_data.get("replicas_before_stop") or provider_data.get("replicas") or 1
            )

            self._logger.info(
                "Kubernetes START: scaling %s %s/%s → %d replicas (machine=%s)",
                resolved_api,
                namespace,
                workload_name,
                target_replicas,
                machine_id,
            )

            try:
                await asyncio.to_thread(
                    self._patch_scale,
                    provider_api=resolved_api,
                    namespace=namespace,
                    name=workload_name,
                    replicas=target_replicas,
                )
                results[machine_id] = True
            except Exception as exc:
                self._logger.error(
                    "Kubernetes START failed for %s %s/%s (machine=%s): %s",
                    resolved_api,
                    namespace,
                    workload_name,
                    machine_id,
                    exc,
                    exc_info=True,
                )
                results[machine_id] = False

        return ProviderResult.success_result(
            {"results": results},
            {"operation": "start_instances"},
        )

    async def _start_single_legacy(self, operation: ProviderOperation) -> ProviderResult:
        """Handle the original single-machine call shape (no machine_coordinates).

        Used by direct unit tests and any caller that builds the operation
        without per-machine coordinate dicts.
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
        """Scale Deployment or StatefulSet workloads to 0 replicas.

        When ``operation.parameters["machine_coordinates"]`` is present (populated
        by :class:`StopMachinesOrchestrator`) each machine's own ``provider_data``
        is used to resolve namespace and workload name.  The per-stop replica
        count is returned under ``data["replicas_before_stop_per_machine"]`` so
        the orchestrator can persist it per machine.  When absent the method
        falls back to the top-level ``provider_data`` / ``provider_api`` keys.

        For Pod and Job ``provider_api`` values the call returns
        ``UNSUPPORTED_OPERATION_FOR_KIND`` immediately.

        Args:
            operation: Provider operation carrying per-machine coordinates
                in ``parameters["machine_coordinates"]`` or top-level keys.

        Returns:
            :class:`ProviderResult` indicating success or an appropriate error code.
            On success, ``data["replicas_before_stop_per_machine"]`` maps each
            machine_id to its replica count before the scale-to-zero.
        """
        machine_coordinates: dict[str, Any] = operation.parameters.get("machine_coordinates") or {}

        # Legacy single-machine path: no machine_coordinates key.
        if not machine_coordinates:
            return await self._stop_single_legacy(operation)

        # Per-machine path from the orchestrator.
        results: dict[str, bool] = {}
        replicas_before_stop_per_machine: dict[str, int] = {}

        for machine_id, coords in machine_coordinates.items():
            provider_data: dict[str, Any] = coords.get("provider_data") or {}
            provider_api: str = coords.get("provider_api") or ""

            if provider_api in _SCALE_UNSUPPORTED_APIS:
                self._logger.warning(
                    "Kubernetes STOP: skipping machine %s — provider_api=%r cannot be "
                    "stopped via replica scaling (Pod/Job)",
                    machine_id,
                    provider_api,
                )
                results[machine_id] = False
                continue

            try:
                namespace, workload_name, resolved_api = self._extract_workload_coords_from_data(
                    provider_data=provider_data,
                    provider_api=provider_api,
                    resource_id=coords.get("resource_id") or "",
                )
            except ValueError as exc:
                self._logger.error(
                    "Kubernetes STOP: cannot resolve workload for machine %s: %s",
                    machine_id,
                    exc,
                )
                results[machine_id] = False
                continue

            current_replicas: int = int(provider_data.get("replicas") or 1)

            self._logger.info(
                "Kubernetes STOP: scaling %s %s/%s → 0 replicas (was %d, machine=%s)",
                resolved_api,
                namespace,
                workload_name,
                current_replicas,
                machine_id,
            )

            try:
                await asyncio.to_thread(
                    self._patch_scale,
                    provider_api=resolved_api,
                    namespace=namespace,
                    name=workload_name,
                    replicas=0,
                )
                results[machine_id] = True
                replicas_before_stop_per_machine[machine_id] = current_replicas
            except Exception as exc:
                self._logger.error(
                    "Kubernetes STOP failed for %s %s/%s (machine=%s): %s",
                    resolved_api,
                    namespace,
                    workload_name,
                    machine_id,
                    exc,
                    exc_info=True,
                )
                results[machine_id] = False

        return ProviderResult.success_result(
            {
                "results": results,
                "replicas_before_stop_per_machine": replicas_before_stop_per_machine,
            },
            {"operation": "stop_instances"},
        )

    async def _stop_single_legacy(self, operation: ProviderOperation) -> ProviderResult:
        """Handle the original single-machine call shape (no machine_coordinates).

        Used by direct unit tests and any caller that builds the operation
        without per-machine coordinate dicts.  Returns the single-machine
        ``replicas_before_stop`` key in data for compatibility.
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

    def _extract_workload_coords_from_data(
        self,
        *,
        provider_data: dict[str, Any],
        provider_api: str,
        resource_id: str,
    ) -> tuple[str, str, str]:
        """Extract namespace, workload name, and canonical provider_api from provider_data.

        Resolution order for workload name:
        1. ``provider_data["deployment_name"]`` (Deployment) or
           ``provider_data["statefulset_name"]`` (StatefulSet)
        2. ``resource_id`` — the acquire-time controller name stamped by the handler

        Args:
            provider_data: The machine's stored provider_data dict.
            provider_api: The originating provider_api string.
            resource_id: The acquire-time controller name (fallback).

        Returns:
            Tuple of ``(namespace, workload_name, resolved_provider_api)``.

        Raises:
            ValueError: When the workload name cannot be determined.
        """
        namespace: str = str(provider_data.get("namespace") or "default")

        resolved_api = provider_api
        if resolved_api not in _SCALE_SUPPORTED_APIS:
            resolved_api = "Deployment"

        if resolved_api == "Deployment":
            workload_name: Optional[str] = (
                str(provider_data["deployment_name"])
                if provider_data.get("deployment_name")
                else None
            )
        else:
            workload_name = (
                str(provider_data["statefulset_name"])
                if provider_data.get("statefulset_name")
                else None
            )

        if not workload_name and resource_id:
            workload_name = resource_id

        if not workload_name:
            raise ValueError(
                f"Cannot determine workload name for {resolved_api} START/STOP.  "
                "Supply provider_data['deployment_name'] / provider_data['statefulset_name'] "
                "or resource_id."
            )

        return namespace, workload_name, resolved_api

    def _extract_workload_coords(
        self, operation: ProviderOperation, provider_api: str
    ) -> tuple[str, str, str]:
        """Extract namespace, workload name, and canonical provider_api from the operation.

        Legacy helper for the single-machine fallback paths.  Reads from the
        top-level ``provider_data`` and ``resource_ids``/``instance_ids`` keys
        in ``operation.parameters``.

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

        resolved_api = provider_api
        if resolved_api not in _SCALE_SUPPORTED_APIS:
            resolved_api = "Deployment"

        if resolved_api == "Deployment":
            workload_name: Optional[str] = (
                str(provider_data["deployment_name"])
                if provider_data.get("deployment_name")
                else None
            )
        else:
            workload_name = (
                str(provider_data["statefulset_name"])
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

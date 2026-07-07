"""Deployment status resolver — read path for :class:`K8sDeploymentHandler`.

Encapsulates the ``check_hosts_status`` flow for the Deployment handler:

* Cache-first read via :meth:`K8sHandlerBase._read_from_cache`.
* Fallback list of pods via ``CoreV1Api.list_namespaced_pod``.
* Always rebase the verdict on the Deployment controller's
  ``availableReplicas`` / ``readyReplicas`` / ``conditions`` so the
  rollup reflects the authoritative scale signal.

The resolver is constructed by :class:`K8sDeploymentHandler` and reuses
the handler's existing helpers (retry, label selector, cache read,
instance-dict mapping) — it carries no state of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler


class DeploymentStatusResolver:
    """Compute the per-request status verdict for a Deployment-managed pod set."""

    def __init__(self, handler: "K8sDeploymentHandler") -> None:
        self._handler = handler

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
        handler = self._handler
        namespace = handler._resolve_request_namespace(request)
        deployment_name = handler._resolve_deployment_name(request)

        cached = handler._read_from_cache(request)
        if cached is not None:
            # When the cache served the per-pod list, still rebase the
            # fulfilment verdict on the Deployment status because the
            # controller's view is authoritative for selective scale.
            cached_instances = handler.apply_pod_timeouts(list(cached.instances))
            controller_view = self.read_deployment_status(namespace, deployment_name)
            fulfilment = self.compute_fulfilment(
                cached_instances,
                request.requested_count,
                controller_view=controller_view,
            )
            return CheckHostsStatusResult(instances=cached_instances, fulfilment=fulfilment)

        selector = handler.build_label_selector(request)

        try:
            response = handler.with_retry(
                handler.client.core_v1.list_namespaced_pod,
                namespace=namespace,
                label_selector=selector,
                operation_name="list_namespaced_pod",
            )
        except Exception as exc:
            handler._logger.error(
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
            handler._instance_dict_for_pod(pod, namespace=namespace) for pod in pods
        ]
        instances = handler.apply_pod_timeouts(instances)
        controller_view = self.read_deployment_status(namespace, deployment_name)
        fulfilment = self.compute_fulfilment(
            instances,
            request.requested_count,
            controller_view=controller_view,
        )
        return CheckHostsStatusResult(instances=instances, fulfilment=fulfilment)

    def read_deployment_status(self, namespace: str, deployment_name: str) -> dict[str, Any]:
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
        handler = self._handler
        try:
            deployment = handler.with_retry(
                handler.client.apps_v1.read_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
                operation_name="read_namespaced_deployment",
            )
        except Exception as exc:
            if handler.is_not_found(exc):
                handler._logger.debug(
                    "Deployment %s in %s not found — assuming pre-create or post-release",
                    deployment_name,
                    namespace,
                )
                return {}
            handler._logger.warning(
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

    def compute_fulfilment(
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


__all__ = ["DeploymentStatusResolver"]

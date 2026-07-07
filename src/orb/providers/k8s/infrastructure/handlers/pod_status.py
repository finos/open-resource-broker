"""Pod status resolver — read path for :class:`K8sPodHandler`.

Encapsulates the ``check_hosts_status`` flow for the bare Pod handler:

* Cache-first read via :meth:`K8sHandlerBase._read_from_cache`.
* Fallback list of pods via ``CoreV1Api.list_namespaced_pod``.
* Verdict is computed entirely from the per-pod phase/ready roll-up —
  there is no controller view to rebase on for stand-alone Pods.

The resolver is constructed by :class:`K8sPodHandler` and reuses the
handler's existing helpers (retry, label selector, cache read,
instance-dict mapping) — it carries no state of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler


class PodStatusResolver:
    """Compute the per-request status verdict for a bare Pod set."""

    def __init__(self, handler: "K8sPodHandler") -> None:
        self._handler = handler

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Return per-pod details + the fulfilment verdict for ``request``.

        Cache-first read path: when a :class:`PodStateCache` has been
        injected and the watcher reports alive, the handler
        reads the cached :class:`PodState` snapshots for ``request_id``.
        A cache miss (no entry) or a stale cache (any entry older than
        :attr:`K8sProviderConfig.stale_cache_timeout_seconds`)
        falls back to a single ``list_namespaced_pod`` call.
        """
        handler = self._handler
        cached = handler._read_from_cache(request)
        if cached is not None:
            cached_instances = handler.apply_pod_timeouts(list(cached.instances))
            fulfilment = self.compute_fulfilment(cached_instances, request.requested_count)
            return CheckHostsStatusResult(instances=cached_instances, fulfilment=fulfilment)

        namespace = handler._resolve_request_namespace(request)
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
                "list_namespaced_pod failed for request %s: %s",
                request.request_id,
                exc,
                exc_info=True,
            )
            # In-flight read failure — treat as in_progress so callers
            # retry rather than failing the request outright.
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
        fulfilment = self.compute_fulfilment(instances, request.requested_count)
        return CheckHostsStatusResult(instances=instances, fulfilment=fulfilment)

    def compute_fulfilment(
        self,
        instances: list[dict[str, Any]],
        requested_count: int,
    ) -> ProviderFulfilment:
        """Roll up per-pod statuses into a :class:`ProviderFulfilment`.

        Mirrors the RunInstances handler's compute helper so the
        downstream presentation is identical.

        A ``"terminated"`` status on a bare-Pod or Job-owned pod means the
        container exited 0 (Kubernetes ``Succeeded`` phase).  Those pods
        count as fulfilled capacity — run-to-completion semantics — so they
        are tallied as ``succeeded_count`` and promote the verdict to
        ``fulfilled`` when the full requested count is satisfied.
        """
        running_count = sum(1 for i in instances if i.get("status") == "running")
        pending_count = sum(1 for i in instances if i.get("status") in ("pending", "starting"))
        failed_count = sum(1 for i in instances if i.get("status") == "failed")
        # Bare pods that exited 0 land here: pod_status_string maps
        # Succeeded → "terminated" for the Pod provider-API.
        succeeded_count = sum(1 for i in instances if i.get("status") == "terminated")

        effective_fulfilled = running_count + succeeded_count

        if effective_fulfilled >= requested_count and failed_count == 0 and requested_count > 0:
            if succeeded_count > 0 and running_count == 0:
                message = f"All {succeeded_count} pod(s) completed successfully"
            elif succeeded_count > 0:
                message = (
                    f"{running_count} running, {succeeded_count} completed "
                    f"({effective_fulfilled}/{requested_count})"
                )
            else:
                message = f"All {running_count} pod(s) running"
            return ProviderFulfilment(
                state="fulfilled",
                message=message,
                target_units=requested_count,
                fulfilled_units=effective_fulfilled,
                running_count=effective_fulfilled,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        if pending_count > 0:
            return ProviderFulfilment(
                state="in_progress",
                message=f"{running_count}/{requested_count} running, {pending_count} pending",
                target_units=requested_count,
                fulfilled_units=running_count,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        if failed_count > 0 and failed_count == len(instances) and len(instances) > 0:
            return ProviderFulfilment(
                state="failed",
                message=f"All {failed_count} pod(s) failed",
                target_units=requested_count,
                fulfilled_units=0,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        if running_count > 0:
            return ProviderFulfilment(
                state="partial",
                message=f"{running_count}/{requested_count} pod(s) running",
                target_units=requested_count,
                fulfilled_units=running_count,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        return ProviderFulfilment(
            state="in_progress",
            message="Pods starting",
            target_units=requested_count,
            fulfilled_units=0,
            running_count=running_count,
            pending_count=pending_count,
            failed_count=failed_count,
        )


__all__ = ["PodStatusResolver"]

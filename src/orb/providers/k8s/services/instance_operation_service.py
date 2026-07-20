"""K8s Instance Operation Service — acquire / return / get_status / cancel.

Extracted from :class:`K8sHandlerRegistry` and :class:`K8sProviderStrategy`
to mirror the AWS ``AWSInstanceOperationService`` pattern.  Owns the typed
provisioning interface (``acquire``, ``return_machines``, ``get_status``) and
the ``cancel_resource`` implementation that tears down in-flight workloads when
a request is cancelled before any machines are allocated.

``cancel_resource`` design
--------------------------
AWS cancels in-flight fleets/ASGs by calling handler-specific ``cancel_resource``
methods.  For Kubernetes the analogous operation is: find every workload that
carries the ``orb.io/request-id=<request_id>`` label and delete it.

Label-based discovery is the canonical reverse-mapping because:

* It is the same mechanism used by the orphan GC and startup reconciler.
* The ``orb.io/request-id`` label is stamped on every workload at create time
  (typed-builder path) and at native-spec stamp time.
* Name parsing is NOT used — the name format may change via K8sNamingConfig,
  so the label is the only stable identifier.

Workload kinds checked: Pod, Deployment, StatefulSet, Job (in that order).
Already-terminated resources (404 from the API server) are handled gracefully.
Partial failures are reported in the return dict but do NOT raise — the caller
always sees ``status="success"`` when at least the attempt was made, and
``status="partial"`` when some deletes failed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.k8s.configuration.config import K8sProviderConfig

if TYPE_CHECKING:  # pragma: no cover
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

# Maximum concurrent delete calls during cancel to avoid flooding the apiserver.
_CANCEL_CONCURRENCY = 20


@dataclass
class CancelResourceResult:
    """Typed result of a :meth:`K8sInstanceOperationService.cancel_resource` call."""

    request_id: str
    deleted: list[str] = field(default_factory=list)
    already_gone: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Return ``"success"``, ``"partial"``, or ``"not_found"`` summary."""
        if not self.deleted and not self.already_gone and not self.failed:
            return "not_found"
        if self.failed:
            return "partial"
        return "success"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the dict shape the strategy returns from execute_operation."""
        return {
            "status": self.status,
            "request_id": self.request_id,
            "deleted": self.deleted,
            "already_gone": self.already_gone,
            "failed": [{"resource": r, "error": e} for r, e in self.failed],
        }


class K8sInstanceOperationService:
    """Service owning cancel_resource for the k8s provider.

    Args:
        config:   Validated :class:`K8sProviderConfig`.
        logger:   Injected :class:`LoggingPort`.
    """

    def __init__(
        self,
        config: K8sProviderConfig,
        logger: LoggingPort,
    ) -> None:
        self._config = config
        self._logger = logger

    # ------------------------------------------------------------------
    # cancel_resource — tear down in-flight workloads by request-id label
    # ------------------------------------------------------------------

    async def cancel_resource(
        self,
        request_id: str,
        kubernetes_client: K8sClient,
        *,
        namespace: Optional[str] = None,
    ) -> CancelResourceResult:
        """Delete all workloads carrying the ``orb.io/request-id=<request_id>`` label.

        Checks Pods, Deployments, StatefulSets, and Jobs in the configured
        namespace (or the supplied *namespace* override).  Already-deleted
        resources (404) are treated as success.  Other API errors are
        recorded in ``result.failed`` but do not raise — the caller always
        receives a typed :class:`CancelResourceResult`.

        Args:
            request_id:         The ORB request UUID whose workloads should
                                be cancelled.
            kubernetes_client:  Live :class:`K8sClient` facade.
            namespace:          Target namespace override.  When ``None``, the
                                provider config's ``namespace`` field is used.

        Returns:
            :class:`CancelResourceResult` with lists of deleted / already_gone
            / failed resource names and an overall ``status`` summary.
        """
        result = CancelResourceResult(request_id=request_id)
        ns = namespace or self._config.namespace or "default"
        label_prefix = self._config.label_prefix
        label_selector = f"{label_prefix}/request-id={request_id}"

        self._logger.info(
            "K8s cancel_resource: scanning namespace=%s for request_id=%s",
            ns,
            request_id,
        )

        # Gather workload names for all four kinds concurrently, then delete.
        try:
            pods_task = asyncio.to_thread(
                self._list_pod_names, kubernetes_client, ns, label_selector
            )
            deployments_task = asyncio.to_thread(
                self._list_deployment_names, kubernetes_client, ns, label_selector
            )
            statefulsets_task = asyncio.to_thread(
                self._list_statefulset_names, kubernetes_client, ns, label_selector
            )
            jobs_task = asyncio.to_thread(
                self._list_job_names, kubernetes_client, ns, label_selector
            )
            pod_names, deploy_names, sts_names, job_names = await asyncio.gather(
                pods_task,
                deployments_task,
                statefulsets_task,
                jobs_task,
                return_exceptions=True,
            )
        except Exception as exc:
            self._logger.error(
                "K8s cancel_resource: list phase failed for request_id=%s: %s",
                request_id,
                exc,
                exc_info=True,
            )
            result.failed.append((f"list:{request_id}", str(exc)))
            return result

        # Flatten and de-duplicate with kind tags so we can route deletes correctly.
        items: list[tuple[str, str]] = []  # (kind, name)
        for kind, names_or_exc in (
            ("Pod", pod_names),
            ("Deployment", deploy_names),
            ("StatefulSet", sts_names),
            ("Job", job_names),
        ):
            if isinstance(names_or_exc, Exception):
                self._logger.warning(
                    "K8s cancel_resource: list %s failed for request_id=%s: %s",
                    kind,
                    request_id,
                    names_or_exc,
                )
                result.failed.append((f"list:{kind}", str(names_or_exc)))
            else:
                for name in list(names_or_exc):  # type: ignore[arg-type]
                    items.append((kind, name))

        if not items:
            self._logger.info(
                "K8s cancel_resource: no workloads found for request_id=%s in namespace=%s",
                request_id,
                ns,
            )
            return result

        self._logger.info(
            "K8s cancel_resource: found %d workload(s) to delete for request_id=%s",
            len(items),
            request_id,
        )

        # Delete concurrently, bounded by _CANCEL_CONCURRENCY.
        sem = asyncio.Semaphore(_CANCEL_CONCURRENCY)

        async def _delete_one(kind: str, name: str) -> None:
            async with sem:
                try:
                    await asyncio.to_thread(
                        self._delete_workload, kubernetes_client, ns, kind, name
                    )
                    result.deleted.append(f"{kind}/{name}")
                    self._logger.debug("K8s cancel_resource: deleted %s/%s", kind, name)
                except Exception as exc:
                    if _is_404(exc):
                        result.already_gone.append(f"{kind}/{name}")
                        self._logger.debug(
                            "K8s cancel_resource: %s/%s already gone (404)", kind, name
                        )
                    else:
                        result.failed.append((f"{kind}/{name}", str(exc)))
                        self._logger.warning(
                            "K8s cancel_resource: failed to delete %s/%s: %s",
                            kind,
                            name,
                            exc,
                        )

        await asyncio.gather(*(_delete_one(kind, name) for kind, name in items))

        self._logger.info(
            "K8s cancel_resource: request_id=%s done — deleted=%d already_gone=%d failed=%d",
            request_id,
            len(result.deleted),
            len(result.already_gone),
            len(result.failed),
        )
        return result

    # ------------------------------------------------------------------
    # List helpers (synchronous — run via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _list_pod_names(self, client: K8sClient, ns: str, label_selector: str) -> list[str]:
        """List Pod names matching *label_selector* in *ns*."""
        resp = client.core_v1.list_namespaced_pod(
            namespace=ns,
            label_selector=label_selector,
        )
        return [p.metadata.name for p in (resp.items or []) if p.metadata and p.metadata.name]

    def _list_deployment_names(self, client: K8sClient, ns: str, label_selector: str) -> list[str]:
        """List Deployment names matching *label_selector* in *ns*."""
        resp = client.apps_v1.list_namespaced_deployment(
            namespace=ns,
            label_selector=label_selector,
        )
        return [d.metadata.name for d in (resp.items or []) if d.metadata and d.metadata.name]

    def _list_statefulset_names(self, client: K8sClient, ns: str, label_selector: str) -> list[str]:
        """List StatefulSet names matching *label_selector* in *ns*."""
        resp = client.apps_v1.list_namespaced_stateful_set(
            namespace=ns,
            label_selector=label_selector,
        )
        return [s.metadata.name for s in (resp.items or []) if s.metadata and s.metadata.name]

    def _list_job_names(self, client: K8sClient, ns: str, label_selector: str) -> list[str]:
        """List Job names matching *label_selector* in *ns*."""
        resp = client.batch_v1.list_namespaced_job(
            namespace=ns,
            label_selector=label_selector,
        )
        return [j.metadata.name for j in (resp.items or []) if j.metadata and j.metadata.name]

    # ------------------------------------------------------------------
    # Delete dispatcher (synchronous — run via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _delete_workload(self, client: K8sClient, ns: str, kind: str, name: str) -> None:
        """Dispatch a delete call for *kind*/*name* in *ns*.

        Raises on any API error including 404 — callers distinguish 404
        from other errors via :func:`_is_404`.
        """
        if kind == "Pod":
            client.core_v1.delete_namespaced_pod(name=name, namespace=ns)
        elif kind == "Deployment":
            client.apps_v1.delete_namespaced_deployment(name=name, namespace=ns)
        elif kind == "StatefulSet":
            client.apps_v1.delete_namespaced_stateful_set(name=name, namespace=ns)
        elif kind == "Job":
            # propagationPolicy=Foreground ensures pods are deleted with the Job.
            from kubernetes.client import V1DeleteOptions  # type: ignore[import-untyped]

            client.batch_v1.delete_namespaced_job(
                name=name,
                namespace=ns,
                body=V1DeleteOptions(propagation_policy="Foreground"),
            )
        else:
            raise ValueError(f"Unknown workload kind: {kind!r}")


def _is_404(exc: BaseException) -> bool:
    """Return ``True`` when *exc* is an ``ApiException`` with status 404."""
    try:
        from kubernetes.client.exceptions import ApiException
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, ApiException) and getattr(exc, "status", None) == 404


__all__ = ["CancelResourceResult", "K8sInstanceOperationService"]

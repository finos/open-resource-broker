"""K8sPodHandler — direct ``v1/Pod`` provisioning handler.

Contract:

* ``acquire_hosts``   — creates N pods concurrently via
  :meth:`CoreV1Api.create_namespaced_pod` wrapped in :func:`asyncio.to_thread`.
* ``check_hosts_status`` — delegated to
  :class:`orb.providers.k8s.handlers.pod_status.PodStatusResolver`, which
  lists pods by ``orb.io/request-id`` label and maps ``status.phase`` to
  an ORB :class:`ProviderFulfilment` verdict.
* ``release_hosts``   — deletes pods by name; 404s are treated as
  best-effort (already gone) and logged at debug.

The handler falls back to on-demand polling when no
:class:`PodStateCache` is wired in.  When a cache is provided the read
path is served from the asyncio watcher's in-memory state instead of
issuing a ``list_namespaced_pod`` per call.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any, Callable, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.infrastructure.handlers.pod_status import PodStatusResolver
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.utilities.pod_spec import (
    build_pod_spec,
    make_pod_name,
)
from orb.providers.k8s.watch.pod_state_cache import PodStateCache

# Cap on concurrent ``create_namespaced_pod`` calls.  The kubernetes
# apiserver can throttle requests aggressively; 50 leaves headroom for
# other components on the same controller.
_MAX_CONCURRENT_CREATES = 50


@injectable
class K8sPodHandler(K8sHandlerBase):
    """Handler for the ``Pod`` provider-API key.

    One ORB capacity unit equals one ``v1/Pod`` with ``restartPolicy: Never``.
    """

    PROVIDER_API: str = "Pod"

    def __init__(
        self,
        kubernetes_client: K8sClient,
        config: K8sProviderConfig,
        logger: LoggingPort,
        max_concurrent_creates: int = _MAX_CONCURRENT_CREATES,
        *,
        pod_state_cache: Optional[PodStateCache] = None,
        cache_alive: Optional[Callable[[], bool]] = None,
        stale_cache_timeout_seconds: Optional[float] = None,
        native_spec_service: Optional[Any] = None,
        node_state_cache: Optional[Any] = None,
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
        )
        self._max_concurrent_creates = max_concurrent_creates
        self._status_resolver = PodStatusResolver(self)

    # ------------------------------------------------------------------
    # acquire_hosts
    # ------------------------------------------------------------------

    async def acquire_hosts(self, request: Request, template: Template) -> dict[str, Any]:
        """Create ``request.requested_count`` pods concurrently.

        Pod naming: ``orb-{request_id[:8]}-{seq:04d}``.  All pods share
        the request-id label so :meth:`check_hosts_status` can list them
        with a single label selector.

        Returns a dict consumed by the strategy's ``acquire`` to build
        the :class:`Accepted` outcome:

        * ``resource_ids`` — list of pod names that were submitted.
        * ``machine_ids``  — identical to ``resource_ids`` for the Pod
          handler.
        * ``provider_data`` — ``{"namespace": ns, "pod_names": [...]}``.
        """
        namespace = self.resolve_namespace(template)
        count = max(int(request.requested_count), 1)
        self._logger.info(
            "Kubernetes pod acquire: request_id=%s namespace=%s count=%s",
            request.request_id,
            namespace,
            count,
        )

        # Native-spec escape hatch (opt-in via K8sProviderConfig.native_spec_enabled):
        # when the operator-supplied template carries a native_spec and
        # the feature is enabled, the rendered dict replaces the body
        # built by build_pod_spec.  Each pod gets a per-pod copy with
        # its unique name / machine-id stamped over the rendered body so
        # the request-scoped label selector still works.
        native_pod_body = (
            self._native_spec_service.process_pod_spec(template, request, namespace=namespace)
            if self._native_spec_service is not None
            else None
        )

        sem = asyncio.Semaphore(self._max_concurrent_creates)
        pods_to_create: list[tuple[str, Any]] = []
        _audited = False
        for seq in range(count):
            pod_name = make_pod_name(str(request.request_id), seq)
            if native_pod_body is not None:
                pod_body: Any = self._stamp_native_pod_body(
                    native_pod_body,
                    pod_name=pod_name,
                    machine_id=pod_name,
                    namespace=namespace,
                    request=request,
                )
            else:
                pod_body = build_pod_spec(
                    template,
                    request,
                    pod_name=pod_name,
                    machine_id=pod_name,  # 1 pod = 1 machine for Pod
                    namespace=namespace,
                    provider_api=self.PROVIDER_API,
                    config=self._config,
                )
            # Audit the spec once per acquire call (all pods share the same
            # spec shape so auditing the first body is sufficient).
            if not _audited:
                self._audit_spec_body(pod_body)
                _audited = True
            pods_to_create.append((pod_name, pod_body))

        results = await asyncio.gather(
            *(
                self._create_one_pod(sem=sem, namespace=namespace, pod_name=name, body=body)
                for name, body in pods_to_create
            ),
            return_exceptions=True,
        )

        created: list[str] = []
        failures: list[tuple[str, str]] = []
        for (pod_name, _), result in zip(pods_to_create, results):
            if isinstance(result, BaseException):
                failures.append((pod_name, str(result)))
                self._logger.warning(
                    "Pod create failed: request_id=%s pod=%s error=%s",
                    request.request_id,
                    pod_name,
                    result,
                )
            else:
                created.append(pod_name)

        if failures and not created:
            # Hard fail — surface the first error as the outcome so callers
            # can present something actionable.
            first_error = failures[0][1]
            raise RuntimeError(
                f"All pod creates failed for request {request.request_id}: {first_error}"
            )

        return {
            "success": True,
            "resource_ids": created,
            "machine_ids": created,
            "provider_data": {
                "request_id": str(request.request_id),
                "namespace": namespace,
                "pod_names": created,
                "failed_pod_names": [name for name, _ in failures],
            },
        }

    def _stamp_native_pod_body(
        self,
        native_body: dict[str, Any],
        *,
        pod_name: str,
        machine_id: str,
        namespace: str,
        request: Request,
    ) -> dict[str, Any]:
        """Stamp per-pod identity onto a rendered native pod body.

        The native_spec is rendered once for the whole acquire call, so
        the same dict would be submitted N times without re-stamping —
        the kubernetes API server would reject the second create as a
        duplicate.  This helper deep-copies the rendered dict and
        overwrites the per-pod fields (name, machine-id label, namespace).

        Operator-controlled non-identity fields (labels, annotations,
        spec.*) are preserved.
        """
        body = copy.deepcopy(native_body)
        metadata = body.setdefault("metadata", {})
        metadata["name"] = pod_name
        metadata["namespace"] = namespace
        labels = dict(metadata.get("labels", {}) or {})
        labels[f"{self._config.label_prefix}/machine-id"] = machine_id
        labels[f"{self._config.label_prefix}/request-id"] = str(request.request_id)
        labels[f"{self._config.label_prefix}/managed"] = "true"
        metadata["labels"] = labels
        return body

    async def _create_one_pod(
        self,
        *,
        sem: asyncio.Semaphore,
        namespace: str,
        pod_name: str,
        body: Any,
    ) -> str:
        """Submit a single ``create_namespaced_pod`` call under the semaphore."""
        async with sem:
            await asyncio.to_thread(
                self.with_retry,
                self.client.core_v1.create_namespaced_pod,
                namespace=namespace,
                body=body,
                operation_name="create_namespaced_pod",
            )
        return pod_name

    # ------------------------------------------------------------------
    # check_hosts_status — delegated to PodStatusResolver
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Delegate the status read path to :class:`PodStatusResolver`."""
        return self._status_resolver.check_hosts_status(request)

    # ------------------------------------------------------------------
    # release_hosts
    # ------------------------------------------------------------------

    async def release_hosts(
        self,
        machine_ids: list[str],
        provider_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Delete the named pods concurrently; 404s are best-effort.

        Mirrors the partial-failure semantics of ``acquire_hosts``:
        individual pod deletes that fail are logged at WARNING and
        collected, but do not abort the remaining deletes.  The method
        only raises when *all* deletes failed, so that orphaned pods are
        surfaced rather than silently dropped.

        Returns a dict with:
        * ``deleted``        — pod names successfully removed.
        * ``failed_deletes`` — pod names that could not be deleted after
          retries (each entry is a ``(name, reason)`` tuple).

        Args:
            machine_ids: Pod names to delete.  For the Pod handler the
                machine_id IS the pod name (1 ORB unit = 1 pod).
            provider_data: The ``provider_data`` dict stamped onto the
                Request aggregate at acquire time.  Carries
                ``namespace`` (falls back to the provider default when
                absent).
        """
        request_id = provider_data.get("request_id", "unknown")
        if not machine_ids:
            self._logger.debug(
                "release_hosts called with no machine_ids for request %s — no-op",
                request_id,
            )
            return {"deleted": [], "failed_deletes": []}

        namespace = self._resolve_namespace_from_provider_data(provider_data)
        self._logger.info(
            "Kubernetes pod release: request_id=%s namespace=%s pods=%s",
            request_id,
            namespace,
            machine_ids,
        )

        sem = asyncio.Semaphore(self._max_concurrent_creates)
        results = await asyncio.gather(
            *(
                self._delete_one_pod(sem=sem, namespace=namespace, pod_name=pid)
                for pid in machine_ids
            ),
            return_exceptions=True,
        )

        deleted: list[str] = []
        failed_deletes: list[tuple[str, str]] = []
        for pod_name, result in zip(machine_ids, results):
            if isinstance(result, BaseException):
                reason = str(result)
                failed_deletes.append((pod_name, reason))
                self._logger.warning(
                    "Pod delete failed: request_id=%s pod=%s reason=%s",
                    request_id,
                    pod_name,
                    reason,
                )
            else:
                deleted.append(pod_name)

        if failed_deletes and not deleted:
            # All deletes failed — raise so the caller can surface the error.
            first_error = failed_deletes[0][1]
            raise RuntimeError(f"All pod deletes failed for request {request_id}: {first_error}")

        return {"deleted": deleted, "failed_deletes": failed_deletes}

    async def _delete_one_pod(
        self,
        *,
        sem: asyncio.Semaphore,
        namespace: str,
        pod_name: str,
    ) -> None:
        """Delete a single pod by name; swallow 404s.

        The first delete attempt runs unwrapped so a 404 from a pod that
        is already gone is detected immediately without wasting retry
        budget.  Other failures fall back to retry-with-backoff via
        :meth:`K8sHandlerBase.with_retry`.
        """
        async with sem:
            try:
                await asyncio.to_thread(
                    self.client.core_v1.delete_namespaced_pod,
                    name=pod_name,
                    namespace=namespace,
                )
                return
            except Exception as exc:
                if self.is_not_found(exc):
                    self._logger.debug(
                        "Pod %s in %s already gone (404) — treating as success",
                        pod_name,
                        namespace,
                    )
                    return
                # Fall through to retry-with-backoff for transient errors.
                self._logger.debug(
                    "Initial delete failed for pod=%s in %s; retrying with backoff: %s",
                    pod_name,
                    namespace,
                    exc,
                )

            try:
                await asyncio.to_thread(
                    self.with_retry,
                    self.client.core_v1.delete_namespaced_pod,
                    name=pod_name,
                    namespace=namespace,
                    operation_name="delete_namespaced_pod",
                )
            except Exception as exc:
                if self.is_not_found(exc):
                    self._logger.debug(
                        "Pod %s in %s already gone (404 after retry) — treating as success",
                        pod_name,
                        namespace,
                    )
                    return
                self._logger.warning(
                    "Pod delete failed: pod=%s namespace=%s error=%s",
                    pod_name,
                    namespace,
                    exc,
                )
                raise

    # ------------------------------------------------------------------
    # Examples
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Return one example template that submits as a ``Pod``."""
        from orb.providers.k8s.domain.template.k8s_template import (  # noqa: PLC0415
            K8sResourceQuantities,
            K8sTemplate,
        )

        return [
            K8sTemplate(
                template_id="k8s-pod-example",
                name="Kubernetes Pod example",
                description="Submit a single pod via the kubernetes provider.",
                provider_api="Pod",
                image_id="busybox:latest",
                max_instances=1,
                resource_requests=K8sResourceQuantities(cpu="100m", memory="128Mi"),
                resource_limits=K8sResourceQuantities(cpu="500m", memory="256Mi"),
                command=["sh", "-c", "sleep 3600"],
            ),
        ]


__all__ = ["K8sPodHandler"]

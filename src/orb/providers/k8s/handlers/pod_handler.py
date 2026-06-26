"""K8sPodHandler — direct ``v1/Pod`` provisioning handler.

Contract:

* ``acquire_hosts``   — creates N pods concurrently via
  :meth:`CoreV1Api.create_namespaced_pod` wrapped in :func:`asyncio.to_thread`.
* ``check_hosts_status`` — lists pods by ``orb.io/request-id`` label and
  maps ``status.phase`` to an ORB :class:`ProviderFulfilment` verdict.
* ``release_hosts``   — deletes pods by name; 404s are treated as
  best-effort (already gone) and logged at debug.

The handler falls back to on-demand polling when no
:class:`PodStateCache` is wired in.  When a cache is provided the read
path is served from the asyncio watcher's in-memory state instead of
issuing a ``list_namespaced_pod`` per call.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.utilities.pod_spec import (
    build_pod_spec,
    make_pod_name,
)
from orb.providers.k8s.watch.pod_state_cache import PodStateCache

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import V1Pod


# Cap on concurrent ``create_namespaced_pod`` calls.  The kubernetes
# apiserver can throttle requests aggressively; 50 leaves headroom for
# other components on the same controller.
_MAX_CONCURRENT_CREATES = 50


# Phase-to-status mapping used by ``_pod_status_string`` and the
# fulfilment computation below.  ``Pending`` covers both "scheduling"
# and "ContainerCreating"; readiness is read separately from the pod
# conditions to disambiguate Running-but-not-Ready.
_TERMINAL_PHASES = frozenset({"Succeeded", "Failed"})


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
    ) -> None:
        super().__init__(
            kubernetes_client=kubernetes_client,
            config=config,
            logger=logger,
            pod_state_cache=pod_state_cache,
            cache_alive=cache_alive,
            stale_cache_timeout_seconds=stale_cache_timeout_seconds,
        )
        self._max_concurrent_creates = max_concurrent_creates

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

        sem = asyncio.Semaphore(self._max_concurrent_creates)
        pods_to_create: list[tuple[str, V1Pod]] = []
        for seq in range(count):
            pod_name = make_pod_name(str(request.request_id), seq)
            pod_body = build_pod_spec(
                template,
                request,
                pod_name=pod_name,
                machine_id=pod_name,  # 1 pod = 1 machine for Pod
                namespace=namespace,
                provider_api=self.PROVIDER_API,
                config=self._config,
            )
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
            "resource_ids": created,
            "machine_ids": created,
            "provider_data": {
                "namespace": namespace,
                "pod_names": created,
                "failed_pod_names": [name for name, _ in failures],
            },
        }

    async def _create_one_pod(
        self,
        *,
        sem: asyncio.Semaphore,
        namespace: str,
        pod_name: str,
        body: "V1Pod",
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
    # check_hosts_status
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Return per-pod details + the fulfilment verdict for ``request``.

        Cache-first read path: when a :class:`PodStateCache` has been
        injected and the watcher reports alive, the handler
        reads the cached :class:`PodState` snapshots for ``request_id``.
        A cache miss (no entry) or a stale cache (any entry older than
        :attr:`K8sProviderConfig.stale_cache_timeout_seconds`)
        falls back to a single ``list_namespaced_pod`` call.
        """
        cached = self._read_from_cache(request)
        if cached is not None:
            cached_instances = self.apply_pod_timeouts(list(cached.instances))
            fulfilment = self._compute_fulfilment(cached_instances, request.requested_count)
            return CheckHostsStatusResult(instances=cached_instances, fulfilment=fulfilment)

        namespace = self._resolve_request_namespace(request)
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
            self._instance_dict_for_pod(pod, namespace=namespace) for pod in pods
        ]
        instances = self.apply_pod_timeouts(instances)
        fulfilment = self._compute_fulfilment(instances, request.requested_count)
        return CheckHostsStatusResult(instances=instances, fulfilment=fulfilment)

    def _compute_fulfilment(
        self,
        instances: list[dict[str, Any]],
        requested_count: int,
    ) -> ProviderFulfilment:
        """Roll up per-pod statuses into a :class:`ProviderFulfilment`.

        Mirrors the RunInstances handler's compute helper so the
        downstream presentation is identical.
        """
        running_count = sum(1 for i in instances if i.get("status") == "running")
        pending_count = sum(1 for i in instances if i.get("status") in ("pending", "starting"))
        failed_count = sum(1 for i in instances if i.get("status") == "failed")

        if running_count >= requested_count and failed_count == 0 and requested_count > 0:
            return ProviderFulfilment(
                state="fulfilled",
                message=f"All {running_count} pod(s) running",
                target_units=requested_count,
                fulfilled_units=running_count,
                running_count=running_count,
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

    # ------------------------------------------------------------------
    # release_hosts
    # ------------------------------------------------------------------

    async def release_hosts(
        self,
        machine_ids: list[str],
        request: Request,
    ) -> None:
        """Delete the named pods concurrently; 404s are best-effort.

        Args:
            machine_ids: Pod names to delete.  For the Pod handler the
                machine_id IS the pod name (1 ORB unit = 1 pod).
            request: Request providing namespace context via
                ``provider_data["namespace"]`` (falls back to the
                provider default).
        """
        if not machine_ids:
            self._logger.debug(
                "release_hosts called with no machine_ids for request %s — no-op",
                request.request_id,
            )
            return

        namespace = self._resolve_request_namespace(request)
        self._logger.info(
            "Kubernetes pod release: request_id=%s namespace=%s pods=%s",
            request.request_id,
            namespace,
            machine_ids,
        )

        sem = asyncio.Semaphore(self._max_concurrent_creates)
        await asyncio.gather(
            *(
                self._delete_one_pod(sem=sem, namespace=namespace, pod_name=pid)
                for pid in machine_ids
            ),
            return_exceptions=False,
        )

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

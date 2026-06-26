"""K8sPodHandler — direct ``v1/Pod`` provisioning handler.

Implements the Phase B contract:

* ``acquire_hosts``   — creates N pods concurrently via
  :meth:`CoreV1Api.create_namespaced_pod` wrapped in :func:`asyncio.to_thread`.
* ``check_hosts_status`` — lists pods by ``orb.io/request-id`` label and
  maps ``status.phase`` to an ORB :class:`ProviderFulfilment` verdict.
* ``release_hosts``   — deletes pods by name; 404s are treated as
  best-effort (already gone) and logged at debug.

The handler uses on-demand polling — no watch task is required.  Phase C
adds the asyncio watcher and switches the read-path over to an
in-memory cache; until then ``check_hosts_status`` issues a single
``list_namespaced_pod`` per call.
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
from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache

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
        )
        self._max_concurrent_creates = max_concurrent_creates
        # Phase C wiring: when both the cache and the ``cache_alive``
        # callable are supplied, :meth:`check_hosts_status` reads from
        # the cache first.  When ``cache_alive()`` is ``False`` (watcher
        # dead) or the cache has no entry for the request (cold start),
        # the handler falls back to a scoped list.  When the cache
        # contains entries but they are older than
        # ``stale_cache_timeout_seconds`` the cache is treated as stale
        # and the same fallback path is taken.
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

        Cache-first read path (Phase C): when a :class:`PodStateCache`
        has been injected and the watcher reports alive, the handler
        reads the cached :class:`PodState` snapshots for ``request_id``.
        A cache miss (no entry) or a stale cache (any entry older than
        :attr:`K8sProviderConfig.stale_cache_timeout_seconds`)
        falls back to a single ``list_namespaced_pod`` call.
        """
        cached = self._read_from_cache(request)
        if cached is not None:
            return cached

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

    def _read_from_cache(self, request: Request) -> Optional[CheckHostsStatusResult]:
        """Cache-first read path.

        Returns:

        * ``None`` when the cache is not wired, the watcher reports
          dead, the cache has no entry for ``request.request_id``
          (cold start), or the cached entries are stale.
        * Otherwise a :class:`CheckHostsStatusResult` computed from
          the cached :class:`PodState` snapshots.

        Stale-entry policy: if *any* cached entry for the request is
        older than ``stale_cache_timeout_seconds`` the entire cache hit
        is rejected and the handler falls back to the list path.  The
        stale entries are dropped from the cache as a side effect so
        subsequent reads do not pay the staleness check repeatedly.
        """
        cache = self._pod_state_cache
        if cache is None:
            return None
        if self._cache_alive is not None and not self._cache_alive():
            return None

        request_id = str(request.request_id)
        # Drop and discard entries older than the staleness window
        # before we consult the cache so the cache hit is consistent.
        dropped = cache.mark_stale(request_id, self._stale_cache_timeout_seconds)
        if dropped:
            self._logger.debug(
                "Dropped %s stale pod cache entr%s for request %s",
                len(dropped),
                "y" if len(dropped) == 1 else "ies",
                request_id,
            )
            # After dropping stale entries the cache may still have
            # fresh entries; fall through to read what is left.

        states = cache.get(request_id)
        if states is None:
            return None

        instances = [self._instance_dict_for_state(state) for state in states]
        instances = self.apply_pod_timeouts(instances)
        fulfilment = self._compute_fulfilment(instances, request.requested_count)
        return CheckHostsStatusResult(instances=instances, fulfilment=fulfilment)

    def _instance_dict_for_state(self, state: PodState) -> dict[str, Any]:
        """Convert a cached :class:`PodState` into the instance-dict shape.

        Mirrors :meth:`_instance_dict_for_pod` so the list-fed and
        cache-fed code paths produce identical dicts downstream.
        """
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

    def _resolve_request_namespace(self, request: Request) -> str:
        """Resolve a request's namespace using saved provider_data when present."""
        provider_data = getattr(request, "provider_data", None) or {}
        if isinstance(provider_data, dict):
            ns = provider_data.get("namespace")
            if isinstance(ns, str) and ns:
                return ns
        return self._config.namespace

    def _instance_dict_for_pod(self, pod: Any, namespace: str) -> dict[str, Any]:
        """Convert a ``V1Pod`` to the per-instance dict shape ORB expects.

        The dict mirrors the AWS provider's ``_format_instance_data``
        output — flat snake_case fields plus a ``provider_data`` block
        for per-handler bookkeeping.
        """
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
        """Return ``True`` iff ``conditions`` has a ``Ready=True`` entry."""
        for cond in conditions:
            ctype = getattr(cond, "type", None)
            cstatus = getattr(cond, "status", None)
            if ctype == "Ready" and cstatus == "True":
                return True
        return False

    @staticmethod
    def _pod_status_string(phase: Optional[str], ready: bool) -> str:
        """Map ``pod.status.phase`` (+ readiness) to an ORB instance-status string.

        The string set mirrors the AWS provider's EC2 instance statuses so
        the downstream domain code (fulfilment math, status display) does
        not need to special-case kubernetes phases.

        * ``Pending``  -> ``"pending"``
        * ``Running`` (not ready)  -> ``"starting"``
        * ``Running`` (ready)      -> ``"running"``
        * ``Succeeded``            -> ``"running"``  (job-style success)
        * ``Failed``               -> ``"failed"``
        * ``Unknown``/None         -> ``"pending"``
        """
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
        """Best-effort extraction of a human-readable status reason.

        Order of preference: terminated container reason, waiting
        container reason, ``PodScheduled=False`` condition reason.
        """
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
        return [
            Template(
                template_id="k8s-pod-example",
                name="Kubernetes Pod example",
                description="Submit a single pod via the kubernetes provider.",
                provider_type="k8s",
                provider_api="Pod",
                image_id="busybox:latest",
                max_instances=1,
                provider_data={
                    "k8s": {
                        "container_image": "busybox:latest",
                        "resource_requests": {"cpu": "100m", "memory": "128Mi"},
                        "resource_limits": {"cpu": "500m", "memory": "256Mi"},
                        "command": ["sh", "-c", "sleep 3600"],
                    },
                },
            ),
        ]


__all__ = ["K8sPodHandler"]

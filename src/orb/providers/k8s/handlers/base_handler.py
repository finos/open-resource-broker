"""Base class for Kubernetes provider handlers.

Mirrors :class:`orb.providers.aws.infrastructure.handlers.base_handler.AWSHandler`
in role: every per-resource-API handler (Pod, Deployment, StatefulSet,
Job) inherits from this class to share client wiring, label-injection,
namespace resolution, and retry helpers.

The base class is intentionally thin — Kubernetes does not need launch
templates, tagging mode toggles, or AMI resolution.  All the heavy
lifting is per-handler.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TypeVar

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.resilience import retry
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.reconciliation.timeout_gc import (
    apply_pod_timeout,
    delete_timed_out_pod_async,
)
from orb.providers.k8s.utilities.pod_spec import request_id_label_selector
from orb.providers.k8s.utilities.pod_state import (
    extract_status_reason,
    is_pod_ready,
    pod_status_string,
)
from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache

T = TypeVar("T")


@injectable
class K8sHandlerBase(ABC):
    """Abstract base for kubernetes provider handlers.

    Subclasses implement the per-resource-API contract:

    * :meth:`acquire_hosts`         — async create the desired pods/workload
    * :meth:`check_hosts_status`    — return :class:`CheckHostsStatusResult`
    * :meth:`release_hosts`         — delete by machine_id list
    * :meth:`get_example_templates` — example templates for ``orb templates``
    """

    # Resource-API key for the handler (e.g. ``"Pod"``).  Used
    # for label injection and reconciler matching.  Subclasses override.
    PROVIDER_API: str = "Kubernetes"

    def __init__(
        self,
        kubernetes_client: K8sClient,
        config: K8sProviderConfig,
        logger: LoggingPort,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        *,
        circuit_breaker_failure_threshold: int = 5,
        circuit_breaker_reset_timeout: int = 60,
        pod_state_cache: Optional[PodStateCache] = None,
        cache_alive: Optional[Callable[[], bool]] = None,
        stale_cache_timeout_seconds: Optional[float] = None,
        native_spec_service: Optional[Any] = None,
    ) -> None:
        self._kubernetes_client = kubernetes_client
        self._config = config
        self._logger = logger
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._cb_failure_threshold = circuit_breaker_failure_threshold
        self._cb_reset_timeout = circuit_breaker_reset_timeout
        # Native-spec escape hatch.  ``None`` when the provider config
        # opts out (``native_spec_enabled=False``) or when the DI
        # resolution failed — handlers fall back to the typed builder
        # path in :mod:`orb.providers.k8s.utilities` when this is unset
        # or when the service reports the hatch disabled.
        self._native_spec_service = native_spec_service
        # Cache wiring: when both the cache and the ``cache_alive``
        # callable are supplied, :meth:`_read_from_cache` returns a
        # per-pod instance list built from the cached snapshots.  When
        # ``cache_alive()`` is ``False`` (watcher dead) or the cache has
        # no entry for the request (cold start), the helper returns
        # ``None`` so the caller falls back to a scoped list.  When the
        # cache contains entries but they are older than
        # ``stale_cache_timeout_seconds`` the cache is treated as stale
        # and the same fallback path is taken.
        self._pod_state_cache = pod_state_cache
        self._cache_alive = cache_alive
        self._stale_cache_timeout_seconds: float = (
            float(stale_cache_timeout_seconds)
            if stale_cache_timeout_seconds is not None
            else float(config.stale_cache_timeout_seconds)
        )

    # ------------------------------------------------------------------
    # Common helpers — used by every concrete handler
    # ------------------------------------------------------------------

    @property
    def client(self) -> K8sClient:
        return self._kubernetes_client

    @property
    def config(self) -> K8sProviderConfig:
        return self._config

    def resolve_namespace(self, template: Template) -> str:
        """Return the namespace this request should target.

        Resolution order:

        1. :attr:`K8sTemplate.namespace` if set (per-template override).
        2. ``K8sProviderConfig.namespace`` (provider default).

        When the provider config has an explicit ``namespaces`` list (the
        multi-namespace mode), the resolved namespace MUST appear in the
        list — otherwise a :class:`ValueError` is raised so the operator
        gets a clear submit-time signal.  ``namespaces=["*"]`` is treated
        as a wildcard and never rejected.
        """
        from orb.providers.k8s.domain.template.k8s_template import (  # noqa: PLC0415
            upcast_to_k8s_template,
        )

        k8s_template = upcast_to_k8s_template(template)
        candidate: Optional[str] = k8s_template.namespace if k8s_template.namespace else None
        if candidate is None:
            candidate = self._config.namespace

        # _resolve_namespace model_validator guarantees this is always a str.
        assert candidate is not None, "namespace must be resolved by model_validator"

        allowed = self._config.namespaces
        if allowed and allowed != ["*"] and candidate not in allowed:
            raise ValueError(
                f"Namespace {candidate!r} is not in the provider's configured "
                f"namespaces list {allowed!r}.  Update the template or the "
                "provider config."
            )
        return candidate

    def build_label_selector(self, request: Request) -> str:
        """Convenience: build the ``label_selector=orb.io/request-id=<id>`` string."""
        return request_id_label_selector(request, label_prefix=self._config.label_prefix)

    def _stamp_native_workload_body(
        self,
        native_body: dict[str, Any],
        *,
        workload_name: str,
        namespace: str,
        replicas: int,
        request: Request,
    ) -> dict[str, Any]:
        """Stamp per-request identity onto a rendered native workload body.

        Used by the Deployment / StatefulSet / Job handlers when the
        native-spec escape hatch is active.  Overwrites the fields that
        ORB owns at acquire time (name / namespace / replicas, request-id
        and managed labels) so the workload remains discoverable by the
        provider's label-selector reads regardless of what the operator
        rendered.  Operator-controlled fields (pod-template selector
        match labels, container spec, ...) are preserved as-is when the
        operator set them.
        """
        body = copy.deepcopy(native_body)

        metadata = body.setdefault("metadata", {})
        metadata["name"] = workload_name
        metadata["namespace"] = namespace
        labels = dict(metadata.get("labels", {}) or {})
        prefix = self._config.label_prefix
        labels[f"{prefix}/managed"] = "true"
        labels[f"{prefix}/request-id"] = str(request.request_id)
        labels[f"{prefix}/template-id"] = str(request.template_id)
        metadata["labels"] = labels

        spec = body.setdefault("spec", {})
        # Stamp the replica count under the field name the workload kind
        # uses.  Job uses ``parallelism`` / ``completions`` (and Jobs do
        # not have a ``replicas`` key), Deployment / StatefulSet use
        # ``replicas``.  We respect whichever key the operator's body
        # already uses; only the present keys are overwritten so that an
        # operator who explicitly set a different value gets the new one.
        if "parallelism" in spec or "completions" in spec:
            spec["parallelism"] = replicas
            spec["completions"] = replicas
        else:
            spec["replicas"] = replicas

        # Always ensure the request-id label is in the pod-template
        # labels too — without it the controller's selector cannot match
        # the pods.  Keep operator-supplied template labels intact.
        template_section = spec.setdefault("template", {})
        template_metadata = template_section.setdefault("metadata", {})
        template_labels = dict(template_metadata.get("labels", {}) or {})
        template_labels[f"{prefix}/request-id"] = str(request.request_id)
        template_labels[f"{prefix}/managed"] = "true"
        template_labels[f"{prefix}/template-id"] = str(request.template_id)
        template_metadata["labels"] = template_labels

        return body

    def apply_pod_timeouts(
        self,
        instances: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Rewrite per-instance dicts for pods stuck Pending past the timeout.

        Wrapper around
        :func:`orb.providers.k8s.reconciliation.timeout_gc.apply_pod_timeout`
        that pulls the timeout from the provider config so handler call
        sites stay one line.  See the timeout_gc module docstring for
        the rewrite semantics — chiefly: ``status="terminated"`` and
        ``provider_data.unschedulable_reason`` populated from
        ``pod.status.conditions``.

        When :attr:`K8sProviderConfig.delete_timed_out_pods` is ``True``
        (the default), each newly-timed-out pod is also scheduled for
        immediate deletion via :func:`delete_timed_out_pod_async`.  The
        deletion is fired as a background asyncio task so the synchronous
        check path is not blocked.  404 responses and other errors are
        swallowed inside the async helper and logged without surfacing.
        When there is no running event loop (CLI / unit-test context) the
        deletion step is silently skipped.
        """
        rewritten = apply_pod_timeout(
            instances,
            pod_timeout_seconds=float(self._config.pod_timeout_seconds),
        )

        if self._config.delete_timed_out_pods:
            self._schedule_timed_out_pod_deletions(instances, rewritten)

        return rewritten

    def _schedule_timed_out_pod_deletions(
        self,
        original: list[dict[str, Any]],
        rewritten: list[dict[str, Any]],
    ) -> None:
        """Schedule fire-and-forget deletion tasks for pods newly timed out.

        Identifies instance dicts that transitioned to
        ``provider_data["timed_out"] = True`` in this pass (i.e. were not
        already marked timed-out on entry) and creates one async deletion
        task per pod.  Requires a running event loop; silently skips when
        none is available (CLI / synchronous test context).
        """
        import asyncio  # noqa: PLC0415

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop — CLI or sync-test context; skip deletion.
            return

        # Build a set of identifiers for pods that were already timed-out on
        # entry so we do not re-schedule their deletions on every poll cycle.
        already_timed_out: set[str] = {
            str(inst.get("instance_id") or inst.get("name") or "")
            for inst in original
            if (inst.get("provider_data") or {}).get("timed_out")
        }

        core_v1 = self._kubernetes_client.core_v1

        for inst in rewritten:
            provider_data: dict[str, Any] = inst.get("provider_data") or {}
            if not provider_data.get("timed_out"):
                continue
            pod_id = str(inst.get("instance_id") or inst.get("name") or "")
            if pod_id in already_timed_out:
                continue

            pod_name: str = str(inst.get("name") or inst.get("instance_id") or "")
            namespace: str = str(provider_data.get("namespace") or self._config.namespace)
            reason: str = str(
                provider_data.get("unschedulable_reason")
                or inst.get("status_reason")
                or "Unschedulable"
            )

            if not pod_name:
                continue

            loop.create_task(
                delete_timed_out_pod_async(
                    core_v1,
                    name=pod_name,
                    namespace=namespace,
                    reason=reason,
                    logger=self._logger,
                )
            )

    def is_not_found(self, exc: BaseException) -> bool:
        """Return ``True`` when ``exc`` is (or wraps) a kubernetes 404 ``ApiException``.

        The retry decorator can wrap the original exception in a
        :class:`MaxRetriesExceededError` whose ``last_exception`` carries
        the genuine ``ApiException``; we unwrap one level to detect 404
        through the retry shell.
        """
        # Lazy import so the architecture test doesn't see a top-level kubernetes import.
        try:
            from kubernetes.client.exceptions import ApiException as _ApiException  # noqa: PLC0415
        except ImportError:  # pragma: no cover — extra not installed
            return False

        candidate: BaseException | None = exc
        # Unwrap one level of retry wrapping when present.
        last_exception = getattr(exc, "last_exception", None)
        if isinstance(last_exception, BaseException):
            candidate = last_exception

        if not isinstance(candidate, _ApiException):
            return False
        status = getattr(candidate, "status", None)
        return status == 404

    def with_retry(
        self,
        operation: Callable[..., T],
        *args: Any,
        operation_name: str = "kubernetes_operation",
        **kwargs: Any,
    ) -> T:
        """Run ``operation`` with circuit-breaker-wrapped exponential-backoff retry.

        Used by handlers for individual SDK calls that should retry on
        transient errors (429 / 5xx).  400 / 403 / 404 / 409 / 410 / 422 are
        raised immediately — ``ExponentialBackoffStrategy.should_retry`` filters
        those non-recoverable Kubernetes API status codes without consuming
        retry budget.

        A per-handler-class circuit breaker is layered on top: once the
        apiserver failure count reaches ``circuit_breaker_failure_threshold``
        the circuit opens and subsequent calls fast-fail with
        ``CircuitBreakerOpenError`` until the reset window expires.  This
        prevents cascading retry storms during apiserver degradation.
        """
        service_key = f"kubernetes.{self.PROVIDER_API.lower()}"

        @retry(
            strategy="circuit_breaker",
            service=service_key,
            max_attempts=self._max_retries,
            base_delay=self._base_delay,
            max_delay=self._max_delay,
            failure_threshold=self._cb_failure_threshold,
            reset_timeout=self._cb_reset_timeout,
        )
        def wrapped() -> T:
            self._logger.debug(
                "Calling Kubernetes operation %s (args=%s kwargs=%s)",
                operation_name,
                args,
                {k: v for k, v in kwargs.items() if k != "body"},
            )
            return operation(*args, **kwargs)

        return wrapped()

    # ------------------------------------------------------------------
    # Pod-state translation — shared between handlers and watcher
    # ------------------------------------------------------------------

    @staticmethod
    def _is_pod_ready(conditions: list[Any]) -> bool:
        """Thin delegate to :func:`pod_state.is_pod_ready` for subclass use."""
        return is_pod_ready(conditions)

    @staticmethod
    def _pod_status_string(phase: Optional[str], ready: bool) -> str:
        """Thin delegate to :func:`pod_state.pod_status_string` for subclass use."""
        return pod_status_string(phase, ready)

    @staticmethod
    def _extract_status_reason(
        container_statuses: list[Any],
        conditions: list[Any],
    ) -> Optional[str]:
        """Thin delegate to :func:`pod_state.extract_status_reason` for subclass use."""
        return extract_status_reason(container_statuses, conditions)

    def _instance_dict_for_pod(self, pod: Any, namespace: str) -> dict[str, Any]:
        """Convert a ``V1Pod`` to the per-instance dict shape ORB expects.

        The dict mirrors the AWS provider's ``_format_instance_data``
        output — flat snake_case fields plus a ``provider_data`` block
        for per-handler bookkeeping.  Shared by every concrete handler
        so the list-fed read path produces identical dicts regardless of
        which workload kind owns the pod.
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

        ready = is_pod_ready(conditions)
        status_str = pod_status_string(phase, ready)
        status_reason = extract_status_reason(container_statuses, conditions)

        # DisruptionTarget condition — Karpenter preemption signal.
        disrupted_reason: Optional[str] = None
        disrupted_message: Optional[str] = None
        for cond in conditions:
            if (
                getattr(cond, "type", None) == "DisruptionTarget"
                and getattr(cond, "status", None) == "True"
            ):
                disrupted_reason = str(getattr(cond, "reason", None) or "")
                disrupted_message = str(getattr(cond, "message", None) or "")
                break

        # Sum restart_count across all containers.
        restart_count: int = sum(
            int(getattr(cs, "restart_count", 0) or 0) for cs in container_statuses
        )

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
                "restart_count": restart_count,
                "disrupted_reason": disrupted_reason,
                "disrupted_message": disrupted_message,
            },
            "metadata": {},
        }

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
                "restart_count": state.restart_count,
                "disrupted_reason": state.disrupted_reason,
                "disrupted_message": state.disrupted_message,
            },
            "metadata": {},
        }

    def _resolve_request_namespace(self, request: Request) -> str:
        """Resolve a request's namespace using saved provider_data when present.

        Falls back to the provider's default namespace when the request
        was not stamped with one — this keeps callers that operate on a
        freshly-loaded Request working without re-querying.
        """
        provider_data = getattr(request, "provider_data", None) or {}
        if isinstance(provider_data, dict):
            ns = provider_data.get("namespace")
            if isinstance(ns, str) and ns:
                return ns
        # _resolve_namespace model_validator guarantees this is always a str.
        namespace = self._config.namespace
        assert namespace is not None, "namespace must be resolved by model_validator"
        return namespace

    def _read_from_cache(self, request: Request) -> Optional[CheckHostsStatusResult]:
        """Cache-first read path.

        Returns:

        * ``None`` when the cache is not wired, the watcher reports
          dead, the cache has no entry for ``request.request_id``
          (cold start), or every cached entry was deemed stale.
        * Otherwise a :class:`CheckHostsStatusResult` whose
          ``instances`` field is the per-instance dict list built from
          the cached snapshots.  ``fulfilment`` is a placeholder that
          the caller MUST replace — either by computing it from the
          per-pod statuses (Pod handler) or by rebasing on the
          controller's view (Deployment / StatefulSet / Job).

        Stale-entry policy: cached entries for the request older than
        ``stale_cache_timeout_seconds`` are dropped before the lookup so
        the cache hit is consistent.  The dropped entries are logged at
        debug level.
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

        states = cache.get(request_id)
        if states is None:
            return None

        instances = [self._instance_dict_for_state(state) for state in states]
        return CheckHostsStatusResult(
            instances=instances,
            fulfilment=ProviderFulfilment(
                state="in_progress",
                message="placeholder (caller rebases fulfilment)",
                target_units=request.requested_count,
            ),
        )

    # ------------------------------------------------------------------
    # Abstract contract — concrete handlers MUST implement
    # ------------------------------------------------------------------

    @abstractmethod
    async def acquire_hosts(self, request: Request, template: Template) -> dict[str, Any]:
        """Asynchronously provision pods/workloads to satisfy ``request``.

        Returns a dict with at minimum:

        * ``resource_ids`` — provider-level resource identifiers
          (pod names for the Pod handler; workload names for
          Deployment/StatefulSet/Job).
        * ``machine_ids``  — per-ORB-unit machine identifiers (typically
          pod names).
        * ``provider_data`` — provider-specific bookkeeping copied onto
          the Request aggregate.
        """

    @abstractmethod
    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Return per-instance details + a :class:`ProviderFulfilment` verdict."""

    @abstractmethod
    async def release_hosts(
        self,
        machine_ids: list[str],
        request: Request,
    ) -> None:
        """Delete the pods/workloads identified by ``machine_ids``."""

    @classmethod
    @abstractmethod
    def get_example_templates(cls) -> list[Template]:
        """Return example templates for this handler's provider-API key."""

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_handler_type(self) -> str:
        """Lower-case handler key derived from the class name."""
        return self.__class__.__name__.replace("Handler", "").lower()

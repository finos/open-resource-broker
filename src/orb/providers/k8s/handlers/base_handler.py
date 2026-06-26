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
from orb.providers.k8s.reconciliation.timeout_gc import apply_pod_timeout
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
        pod_state_cache: Optional[PodStateCache] = None,
        cache_alive: Optional[Callable[[], bool]] = None,
        stale_cache_timeout_seconds: Optional[float] = None,
    ) -> None:
        self._kubernetes_client = kubernetes_client
        self._config = config
        self._logger = logger
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
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
        """
        return apply_pod_timeout(
            instances,
            pod_timeout_seconds=float(self._config.pod_timeout_seconds),
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
        """Run ``operation`` with exponential-backoff retry.

        Used by handlers for individual SDK calls that should retry on
        transient errors (429 / 5xx).  404 / 400 / 403 / 422 are raised
        immediately — the retry decorator does not catch those because
        the underlying ``ApiException`` carries the HTTP status and the
        retry strategy filters non-recoverable codes by re-raising.
        """

        @retry(
            strategy="exponential",
            service=f"kubernetes.{self.PROVIDER_API.lower()}",
            max_attempts=self._max_retries,
            base_delay=self._base_delay,
            max_delay=self._max_delay,
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
        return self._config.namespace

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

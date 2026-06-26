"""Base class for Kubernetes provider handlers.

Mirrors :class:`orb.providers.aws.infrastructure.handlers.base_handler.AWSHandler`
in role: every per-resource-API handler (Pod in Phase B; Deployment /
StatefulSet / Job in later phases) inherits from this class to share
client wiring, label-injection, namespace resolution, and retry helpers.

The base class is intentionally thin — Kubernetes does not need launch
templates, tagging mode toggles, or AMI resolution.  All the heavy
lifting is per-handler.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TypeVar

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.resilience import retry
from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.infrastructure.kubernetes_client import KubernetesClient
from orb.providers.kubernetes.reconciliation.timeout_gc import apply_pod_timeout
from orb.providers.kubernetes.utilities.pod_spec import request_id_label_selector

T = TypeVar("T")


@injectable
class KubernetesHandlerBase(ABC):
    """Abstract base for kubernetes provider handlers.

    Subclasses implement the per-resource-API contract:

    * :meth:`acquire_hosts`         — async create the desired pods/workload
    * :meth:`check_hosts_status`    — return :class:`CheckHostsStatusResult`
    * :meth:`release_hosts`         — delete by machine_id list
    * :meth:`get_example_templates` — example templates for ``orb templates``
    """

    # Resource-API key for the handler (e.g. ``"KubernetesPod"``).  Used
    # for label injection and reconciler matching.  Subclasses override.
    PROVIDER_API: str = "Kubernetes"

    def __init__(
        self,
        kubernetes_client: KubernetesClient,
        config: KubernetesProviderConfig,
        logger: LoggingPort,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
    ) -> None:
        self._kubernetes_client = kubernetes_client
        self._config = config
        self._logger = logger
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay

    # ------------------------------------------------------------------
    # Common helpers — used by every concrete handler
    # ------------------------------------------------------------------

    @property
    def client(self) -> KubernetesClient:
        return self._kubernetes_client

    @property
    def config(self) -> KubernetesProviderConfig:
        return self._config

    def resolve_namespace(self, template: Template) -> str:
        """Return the namespace this request should target.

        Resolution order:

        1. ``template.provider_data["kubernetes"]["namespace"]`` if set
           (per-template override).
        2. ``KubernetesProviderConfig.namespace`` (provider default).

        When the provider config has an explicit ``namespaces`` list (the
        multi-namespace mode), the resolved namespace MUST appear in the
        list — otherwise a :class:`ValueError` is raised so the operator
        gets a clear submit-time signal.  ``namespaces=["*"]`` is treated
        as a wildcard and never rejected.
        """
        provider_data = getattr(template, "provider_data", None) or {}
        k8s_block = provider_data.get("kubernetes") if isinstance(provider_data, dict) else None
        candidate: Optional[str] = None
        if isinstance(k8s_block, dict):
            ns = k8s_block.get("namespace")
            if isinstance(ns, str) and ns:
                candidate = ns
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
        :func:`orb.providers.kubernetes.reconciliation.timeout_gc.apply_pod_timeout`
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

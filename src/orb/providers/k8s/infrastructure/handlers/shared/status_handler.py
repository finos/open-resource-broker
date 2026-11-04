"""Handler surface used by the workload status resolvers.

Each status resolver (deployment, job, pod, stateful set) reuses a small set of
helpers that every concrete handler inherits from
:class:`~orb.providers.k8s.infrastructure.handlers.base_handler.K8sHandlerBase`.
Typing the resolver against these protocols, instead of the concrete handler
class, keeps the import direction one-way: handlers import their resolver,
resolvers never import their handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, ContextManager, Optional, Protocol, TypeVar

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
    from orb.domain.request.aggregate import Request
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

T = TypeVar("T")


class StatusResolverHandler(Protocol):
    """Helpers every workload handler provides to its status resolver."""

    _logger: LoggingPort

    @property
    def client(self) -> K8sClient:  # type: ignore[return]
        pass

    @property
    def config(self) -> K8sProviderConfig:  # type: ignore[return]
        pass

    def _timed_api_call(self, operation: str) -> ContextManager[None]:  # type: ignore[return]
        pass

    def _record_api_exception(self, exc: BaseException, *, operation: str) -> None:
        pass

    def build_label_selector(self, request: Request) -> str:  # type: ignore[return]
        pass

    def apply_pod_timeouts(self, instances: list[dict[str, Any]]) -> list[dict[str, Any]]:  # type: ignore[return]
        pass

    def is_not_found(self, exc: BaseException) -> bool:  # type: ignore[return]
        pass

    def with_retry(
        self,
        operation: Callable[..., T],
        *args: Any,
        operation_name: str = "kubernetes_operation",
        **kwargs: Any,
    ) -> T:  # type: ignore[return]
        pass

    def _instance_dict_for_pod(self, pod: Any, namespace: str) -> dict[str, Any]:  # type: ignore[return]
        pass

    def _resolve_request_namespace(self, request: Request) -> str:  # type: ignore[return]
        pass

    def _read_from_cache(self, request: Request) -> Optional[CheckHostsStatusResult]:  # type: ignore[return]
        pass


class DeploymentStatusHandler(StatusResolverHandler, Protocol):
    """Handler surface for the Deployment status resolver."""

    def _resolve_deployment_name(self, request: Request) -> str:  # type: ignore[return]
        pass


class JobStatusHandler(StatusResolverHandler, Protocol):
    """Handler surface for the Job status resolver."""

    def _resolve_job_name(self, request: Request) -> str:  # type: ignore[return]
        pass


class StatefulSetStatusHandler(StatusResolverHandler, Protocol):
    """Handler surface for the StatefulSet status resolver."""

    def _resolve_statefulset_name(self, request: Request) -> str:  # type: ignore[return]
        pass

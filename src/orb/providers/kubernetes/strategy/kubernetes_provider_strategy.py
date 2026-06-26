"""Kubernetes Provider Strategy — orchestrator for Kubernetes provider operations.

Mirrors :class:`orb.providers.aws.strategy.aws_provider_strategy.AWSProviderStrategy`
in shape and responsibility split.  Phase A implements the wired-up shell:

* ``check_health`` — calls ``CoreV1Api.get_api_resources`` and returns a
  populated :class:`ProviderHealthStatus`.
* ``get_capabilities`` — advertises support for the three core operation
  types (``CREATE_INSTANCES``, ``TERMINATE_INSTANCES``, ``GET_INSTANCE_STATUS``)
  plus the four v1 handler names.
* ``get_available_regions`` — returns ``[]`` because Kubernetes uses
  contexts rather than regions.
* ``acquire`` / ``return_machines`` / ``get_status`` — raise
  ``NotImplementedError`` (filled in Phase B with the Pod handler).

The strategy adopts the same constructor signature, lazy-getter style and
DI-friendly contract as the AWS counterpart so that the registration
factory can be a near drop-in.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.operation_outcome import OperationOutcome
from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.providers.base.strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)
from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.infrastructure.kubernetes_client import KubernetesClient
from orb.providers.kubernetes.value_objects import KubernetesProviderApi

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.request.aggregate import Request
    from orb.monitoring.health import HealthCheck


@injectable
class KubernetesProviderStrategy(ProviderStrategy):
    """Kubernetes implementation of the :class:`ProviderStrategy` interface.

    Phase A wires the shell: config validation, lazy KubernetesClient
    construction, health check, capabilities, and the typed
    provisioning interface stubs.  Phase B onwards fills in the handler
    services and the typed ``acquire`` / ``return_machines`` / ``get_status``
    paths.
    """

    _SUPPORTED_APIS: tuple[str, ...] = tuple(api.value for api in KubernetesProviderApi)

    def __init__(
        self,
        config: KubernetesProviderConfig,
        logger: LoggingPort,
        provider_name: Optional[str] = None,
        provider_instance_config: Optional[Any] = None,
        config_port: Optional[ConfigurationPort] = None,
        console: Optional[Any] = None,
        kubernetes_client: Optional[KubernetesClient] = None,
    ) -> None:
        if not isinstance(config, KubernetesProviderConfig):
            raise ValueError("KubernetesProviderStrategy requires KubernetesProviderConfig")

        super().__init__(config)
        self._logger = logger
        self._k8s_config = config
        self._console = console
        self._provider_instance_config = provider_instance_config
        self._provider_name = provider_name
        self._config_port = config_port
        self._kubernetes_client: Optional[KubernetesClient] = kubernetes_client

    # ------------------------------------------------------------------
    # Provider identity
    # ------------------------------------------------------------------

    @property
    def provider_type(self) -> str:
        return "kubernetes"

    @property
    def provider_name(self) -> Optional[str]:
        return self._provider_name

    @property
    def kubernetes_client(self) -> KubernetesClient:
        """Lazy ``KubernetesClient`` accessor.

        Constructs the client on first access using the validated provider
        config and the injected logger.  Unit tests can pre-supply a mock
        client via the ``kubernetes_client`` constructor argument.
        """
        if self._kubernetes_client is None:
            self._kubernetes_client = KubernetesClient(
                config=self._k8s_config,
                logger=self._logger,
            )
        return self._kubernetes_client

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        try:
            self._logger.info(
                "Kubernetes provider strategy ready (namespace=%s, in_cluster=%s)",
                self._k8s_config.namespace,
                self._k8s_config.in_cluster,
            )
            self._initialized = True
            return True
        except Exception as exc:
            self._logger.error(
                "Failed to initialize Kubernetes provider strategy: %s", exc, exc_info=True
            )
            return False

    def cleanup(self) -> None:
        try:
            if self._kubernetes_client is not None:
                self._kubernetes_client.cleanup()
            self._kubernetes_client = None
            self._initialized = False
        except Exception as exc:
            self._logger.warning(
                "Failed during Kubernetes provider cleanup: %s", exc, exc_info=True
            )

    # ------------------------------------------------------------------
    # Operation dispatch — Phase B onwards
    # ------------------------------------------------------------------

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """Execute a provider operation.

        Phase A only services ``HEALTH_CHECK`` end-to-end; the resource
        lifecycle operations return an ``UNSUPPORTED_OPERATION`` error
        with a clear message until the handler layer arrives in Phase B.
        """
        self._logger.debug("Kubernetes strategy executing operation: %s", operation.operation_type)

        if not self._initialized:
            return ProviderResult.error_result(
                "Kubernetes provider strategy not initialized", "NOT_INITIALIZED"
            )

        start_time = time.time()
        try:
            if operation.operation_type == ProviderOperationType.HEALTH_CHECK:
                health = self.check_health()
                result = ProviderResult.success_result(
                    {
                        "is_healthy": health.is_healthy,
                        "status_message": health.status_message,
                        "response_time_ms": health.response_time_ms,
                    },
                    {"operation": "health_check"},
                )
            else:
                result = ProviderResult.error_result(
                    f"Operation {operation.operation_type} not yet supported by the "
                    "kubernetes provider (lands in Phase B+).",
                    "UNSUPPORTED_OPERATION",
                )

            execution_time_ms = int((time.time() - start_time) * 1000)
            return result.model_copy(
                update={
                    "routing_info": {
                        "execution_time_ms": execution_time_ms,
                        "provider": "kubernetes",
                    },
                    "metadata": {
                        **result.metadata,
                        "execution_time_ms": execution_time_ms,
                        "provider": "kubernetes",
                    },
                }
            )
        except Exception as exc:
            execution_time_ms = int((time.time() - start_time) * 1000)
            self._logger.error("Kubernetes operation failed: %s", exc, exc_info=True)
            return ProviderResult.error_result(
                f"Kubernetes operation failed: {exc}",
                "OPERATION_FAILED",
            ).model_copy(
                update={
                    "routing_info": {
                        "execution_time_ms": execution_time_ms,
                        "provider": "kubernetes",
                    }
                }
            )

    # ------------------------------------------------------------------
    # Capabilities & health
    # ------------------------------------------------------------------

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="kubernetes",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.HEALTH_CHECK,
            ],
            supported_apis=list(self._SUPPORTED_APIS),
            features={
                "selective_termination": True,
                "watch_supported": True,
                "namespaces_supported": True,
            },
        )

    def check_health(self) -> ProviderHealthStatus:
        """Probe the Kubernetes API server via ``CoreV1Api.get_api_resources``."""
        start = time.time()
        try:
            resources = self.kubernetes_client.core_v1.get_api_resources()
            response_time_ms = (time.time() - start) * 1000.0
            resource_count = len(getattr(resources, "resources", []) or [])
            return ProviderHealthStatus.healthy(
                message=(f"Kubernetes API server reachable; {resource_count} core/v1 resources"),
                response_time_ms=response_time_ms,
            )
        except Exception as exc:
            response_time_ms = (time.time() - start) * 1000.0
            self._logger.warning("Kubernetes health check failed: %s", exc, exc_info=True)
            return ProviderHealthStatus.unhealthy(
                message=f"Kubernetes API server unreachable: {exc}",
                error_details={
                    "error": str(exc),
                    "response_time_ms": response_time_ms,
                },
            )

    # ------------------------------------------------------------------
    # Naming
    # ------------------------------------------------------------------

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        """Generate a Kubernetes provider instance name.

        Pattern: ``kubernetes_{context_or_namespace}``.  When neither is
        usable, falls back to ``kubernetes_default``.
        """
        context = config.get("context")
        if context:
            return f"kubernetes_{context}"
        namespace = config.get("namespace", "default")
        return f"kubernetes_{namespace}"

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        """Inverse of :meth:`generate_provider_name`."""
        if not provider_name.startswith("kubernetes_"):
            return {}
        suffix = provider_name[len("kubernetes_") :]
        return {"context_or_namespace": suffix}

    def get_provider_name_pattern(self) -> str:
        return "kubernetes_{context_or_namespace}"

    def get_supported_apis(self) -> list[str]:
        return list(self._SUPPORTED_APIS)

    # ------------------------------------------------------------------
    # Region / CLI helpers
    # ------------------------------------------------------------------

    def get_available_regions(self) -> list[tuple[str, str]]:
        """Kubernetes has contexts, not regions — return an empty list."""
        return []

    def get_default_region(self) -> str:
        """Kubernetes has no region concept; return an empty string."""
        return ""

    def get_cli_extra_config_keys(self) -> set[str]:
        return set()

    def get_cli_infrastructure_defaults(self, args: Any) -> dict[str, Any]:
        return {}

    # ------------------------------------------------------------------
    # Health-check integration
    # ------------------------------------------------------------------

    def register_health_checks(self, health_check: "HealthCheck") -> None:
        """Register Kubernetes-specific health checks if the client is reachable."""
        try:
            client = self.kubernetes_client
        except Exception as exc:
            self._logger.debug(
                "Skipping Kubernetes health-check registration: %s", exc, exc_info=True
            )
            return

        from orb.providers.kubernetes.health import register_kubernetes_health_checks

        register_kubernetes_health_checks(health_check, client)

    # ------------------------------------------------------------------
    # Typed provisioning interface — Phase B+
    # ------------------------------------------------------------------

    async def acquire(self, request: "Request") -> OperationOutcome:
        """Submit an acquisition request to Kubernetes — filled in Phase B."""
        raise NotImplementedError(
            "KubernetesProviderStrategy.acquire is implemented in Phase B (Pod handler)."
        )

    async def return_machines(self, machine_ids: list[str], request: "Request") -> OperationOutcome:
        """Submit a return (termination) request to Kubernetes — filled in Phase B."""
        raise NotImplementedError(
            "KubernetesProviderStrategy.return_machines is implemented in Phase B (Pod handler)."
        )

    async def get_status(self, resource_ids: list[str], request: "Request") -> OperationOutcome:
        """Query Kubernetes resource status — filled in Phase B."""
        raise NotImplementedError(
            "KubernetesProviderStrategy.get_status is implemented in Phase B (Pod handler)."
        )

    def __str__(self) -> str:  # pragma: no cover — trivial
        return (
            "KubernetesProviderStrategy("
            f"namespace={self._k8s_config.namespace}, "
            f"initialized={self._initialized})"
        )

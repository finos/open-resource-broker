"""Kubernetes Provider Strategy — orchestrator for Kubernetes provider operations.

Mirrors :class:`orb.providers.aws.strategy.aws_provider_strategy.AWSProviderStrategy`
in shape and responsibility split:

* ``check_health`` — calls ``CoreV1Api.get_api_resources`` and returns a
  populated :class:`ProviderHealthStatus`.
* ``get_capabilities`` — advertises support for the three core operation
  types (``CREATE_INSTANCES``, ``TERMINATE_INSTANCES``, ``GET_INSTANCE_STATUS``)
  plus the four v1 handler names.
* ``get_available_regions`` — returns ``[]`` because Kubernetes uses
  contexts rather than regions.
* ``acquire`` / ``return_machines`` / ``get_status`` — dispatch to the
  per-provider-API handler.  Phase B wires the Pod handler; Deployment /
  StatefulSet / Job handlers arrive in Phases E and F.

The strategy adopts the same constructor signature, lazy-getter style and
DI-friendly contract as the AWS counterpart so that the registration
factory can be a near drop-in.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.operation_outcome import Accepted, Completed, Failed, OperationOutcome
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
from orb.providers.kubernetes.handlers.base_handler import KubernetesHandlerBase
from orb.providers.kubernetes.handlers.deployment_handler import KubernetesDeploymentHandler
from orb.providers.kubernetes.handlers.job_handler import KubernetesJobHandler
from orb.providers.kubernetes.handlers.pod_handler import KubernetesPodHandler
from orb.providers.kubernetes.handlers.statefulset_handler import KubernetesStatefulSetHandler
from orb.providers.kubernetes.infrastructure.kubernetes_client import KubernetesClient
from orb.providers.kubernetes.value_objects import KubernetesProviderApi
from orb.providers.kubernetes.watch.multi_namespace import MultiNamespaceWatcher

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.request.aggregate import Request
    from orb.domain.template.template_aggregate import Template
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
        handler_overrides: Optional[dict[str, KubernetesHandlerBase]] = None,
        watch_manager: Optional[MultiNamespaceWatcher] = None,
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
        # Handler cache keyed by provider_api value.  Tests can pre-seed
        # this via ``handler_overrides`` to inject mock handlers.
        self._handlers: dict[str, KubernetesHandlerBase] = dict(handler_overrides or {})
        # Watch fan-out.  Constructed lazily by :meth:`initialize` when
        # ``config.watch_enabled`` is True and no override has been
        # provided.  Tests inject a stub via ``watch_manager``.
        self._watch_manager: Optional[MultiNamespaceWatcher] = watch_manager

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
            self._maybe_start_watch_manager()
            self._initialized = True
            return True
        except Exception as exc:
            self._logger.error(
                "Failed to initialize Kubernetes provider strategy: %s", exc, exc_info=True
            )
            return False

    def cleanup(self) -> None:
        try:
            if self._watch_manager is not None:
                # ``stop`` is async; schedule it on the running loop if
                # there is one, otherwise drive it synchronously via
                # ``asyncio.run``.  CLI cleanup paths typically have no
                # loop running while daemon paths do.
                self._stop_watch_manager_sync()
            if self._kubernetes_client is not None:
                self._kubernetes_client.cleanup()
            self._kubernetes_client = None
            self._initialized = False
        except Exception as exc:
            self._logger.warning(
                "Failed during Kubernetes provider cleanup: %s", exc, exc_info=True
            )

    def _maybe_start_watch_manager(self) -> None:
        """Start the watch fleet when enabled by config and a loop is available.

        The fleet runs as an asyncio task and therefore needs a running
        event loop.  When ``initialize`` is called from a synchronous
        context (e.g. CLI bootstrap) we skip startup and let the
        cache-less fallback path serve reads.  Daemon / REST callers
        typically run inside an event loop and pick up the watcher.
        """
        if not self._k8s_config.watch_enabled:
            return
        if self._watch_manager is None:
            self._watch_manager = MultiNamespaceWatcher(
                kubernetes_client=self.kubernetes_client,
                config=self._k8s_config,
                logger=self._logger,
            )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._logger.debug(
                "Skipping Kubernetes watcher startup: no running event loop "
                "(cache-less fallback will serve reads)."
            )
            return
        try:
            self._watch_manager.start()
        except Exception as exc:
            self._logger.warning("Failed to start Kubernetes watcher fleet: %s", exc, exc_info=True)

    def _stop_watch_manager_sync(self) -> None:
        """Stop the watch manager from a sync-or-async cleanup context."""
        manager = self._watch_manager
        if manager is None or not manager.is_started():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            try:
                asyncio.run(manager.stop())
            except Exception as exc:
                self._logger.debug(
                    "Watch manager stop raised during cleanup: %s", exc, exc_info=True
                )
            return
        # Inside a running loop — schedule stop and let the loop drain.
        loop.create_task(manager.stop())

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
    # Handler dispatch
    # ------------------------------------------------------------------

    def _resolve_provider_api(self, request: "Request") -> str:
        """Pick the provider-API key for ``request``.

        Defaults to :attr:`KubernetesProviderApi.POD` so legacy callers
        that omit the provider_api field still route to the Pod handler.
        """
        api = getattr(request, "provider_api", None)
        if api:
            return str(api)
        return KubernetesProviderApi.POD.value

    def _get_handler(self, provider_api: str) -> KubernetesHandlerBase:
        """Return (and lazily construct) the handler for ``provider_api``."""
        handler = self._handlers.get(provider_api)
        if handler is not None:
            return handler
        cache = self._watch_manager.cache if self._watch_manager is not None else None
        alive = (
            (lambda m=self._watch_manager: m.is_healthy())
            if self._watch_manager is not None
            else None
        )
        if provider_api == KubernetesProviderApi.POD.value:
            handler = KubernetesPodHandler(
                kubernetes_client=self.kubernetes_client,
                config=self._k8s_config,
                logger=self._logger,
                pod_state_cache=cache,
                cache_alive=alive,
            )
            self._handlers[provider_api] = handler
            return handler
        if provider_api == KubernetesProviderApi.DEPLOYMENT.value:
            handler = KubernetesDeploymentHandler(
                kubernetes_client=self.kubernetes_client,
                config=self._k8s_config,
                logger=self._logger,
                pod_state_cache=cache,
                cache_alive=alive,
            )
            self._handlers[provider_api] = handler
            return handler
        if provider_api == KubernetesProviderApi.STATEFUL_SET.value:
            handler = KubernetesStatefulSetHandler(
                kubernetes_client=self.kubernetes_client,
                config=self._k8s_config,
                logger=self._logger,
                pod_state_cache=cache,
                cache_alive=alive,
            )
            self._handlers[provider_api] = handler
            return handler
        if provider_api == KubernetesProviderApi.JOB.value:
            handler = KubernetesJobHandler(
                kubernetes_client=self.kubernetes_client,
                config=self._k8s_config,
                logger=self._logger,
                pod_state_cache=cache,
                cache_alive=alive,
            )
            self._handlers[provider_api] = handler
            return handler
        raise NotImplementedError(
            f"Kubernetes handler for provider_api={provider_api!r} is not yet implemented "
            "(Pod, Deployment, StatefulSet and Job are implemented)."
        )

    # ------------------------------------------------------------------
    # Typed provisioning interface
    # ------------------------------------------------------------------

    async def acquire(self, request: "Request") -> OperationOutcome:
        """Submit an acquisition request to Kubernetes via the per-API handler."""
        try:
            provider_api = self._resolve_provider_api(request)
            handler = self._get_handler(provider_api)
            template = self._build_template_for_request(request)
            result = await handler.acquire_hosts(request, template)

            resource_ids = list(result.get("resource_ids", []) or [])
            machine_ids = list(result.get("machine_ids", []) or [])
            metadata: dict[str, Any] = {
                "provider_api": provider_api,
                "provider_data": result.get("provider_data", {}),
                "machine_ids": machine_ids,
            }
            self._logger.info(
                "Kubernetes acquire accepted: request_id=%s pods=%s",
                request.request_id,
                resource_ids,
            )
            return Accepted(
                request_id=str(request.request_id),
                pending_resource_ids=resource_ids,
                metadata=metadata,
            )
        except Exception as exc:
            self._logger.error("Kubernetes acquire failed: %s", exc, exc_info=True)
            return Failed(error=str(exc), recoverable=False)

    async def return_machines(self, machine_ids: list[str], request: "Request") -> OperationOutcome:
        """Delete the named pods via the per-API handler."""
        try:
            provider_api = self._resolve_provider_api(request)
            handler = self._get_handler(provider_api)
            await handler.release_hosts(list(machine_ids), request)
            self._logger.info(
                "Kubernetes return accepted: request_id=%s machine_ids=%s",
                request.request_id,
                machine_ids,
            )
            return Accepted(
                request_id=str(request.request_id),
                pending_resource_ids=list(machine_ids),
                metadata={"provider_api": provider_api},
            )
        except Exception as exc:
            self._logger.error("Kubernetes return_machines failed: %s", exc, exc_info=True)
            return Failed(error=str(exc), recoverable=False)

    async def get_status(self, resource_ids: list[str], request: "Request") -> OperationOutcome:
        """Poll the per-API handler's ``check_hosts_status`` for a verdict.

        Returns ``Completed`` when fulfilment is terminal (``fulfilled``,
        ``partial``, or ``failed``); ``Accepted`` while ``in_progress``.
        """
        try:
            provider_api = self._resolve_provider_api(request)
            handler = self._get_handler(provider_api)
            check_result = await asyncio.to_thread(handler.check_hosts_status, request)
            instances = check_result.instances
            fulfilment = check_result.fulfilment

            metadata: dict[str, Any] = {
                "provider_api": provider_api,
                "fulfilment": fulfilment,
                "instances": instances,
            }

            if fulfilment.state == "in_progress":
                pending = [
                    i.get("instance_id", "")
                    for i in instances
                    if i.get("status") in ("pending", "starting")
                ]
                return Accepted(
                    request_id=str(request.request_id),
                    pending_resource_ids=pending or list(resource_ids),
                    metadata=metadata,
                )

            terminal_ids = [i.get("instance_id", "") for i in instances]
            return Completed(resource_ids=terminal_ids, metadata=metadata)
        except Exception as exc:
            self._logger.error("Kubernetes get_status failed: %s", exc, exc_info=True)
            return Failed(error=str(exc), recoverable=True)

    def _build_template_for_request(self, request: "Request") -> "Template":
        """Resolve the :class:`Template` carried by ``request``.

        Phase B does not yet wire the full template-aggregate registration
        (lands in Phase G).  For now we pick up the template payload from
        ``request.metadata['template']`` (REST/CLI submission shape) and
        fall back to a minimal template assembled from request fields.
        """
        from orb.domain.template.template_aggregate import Template as _Template  # noqa: PLC0415

        meta = getattr(request, "metadata", None) or {}
        if isinstance(meta, dict):
            template_payload = meta.get("template")
            if isinstance(template_payload, _Template):
                return template_payload
            if isinstance(template_payload, dict):
                return _Template(**template_payload)

        # Fall back to a minimal template built from the request fields.
        return _Template(
            template_id=str(request.template_id),
            provider_type="kubernetes",
            provider_api=request.provider_api or KubernetesProviderApi.POD.value,
            max_instances=max(int(request.requested_count), 1),
        )

    def __str__(self) -> str:  # pragma: no cover — trivial
        return (
            "KubernetesProviderStrategy("
            f"namespace={self._k8s_config.namespace}, "
            f"initialized={self._initialized})"
        )

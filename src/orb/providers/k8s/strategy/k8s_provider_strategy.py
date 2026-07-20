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
* ``acquire`` / ``return_machines`` / ``get_status`` — dispatched through
  :class:`K8sHandlerRegistry` which selects the per-provider-API handler
  (Pod / Deployment / StatefulSet / Job) and resolves the Template payload.

The strategy adopts the same constructor signature, lazy-getter style and
DI-friendly contract as the AWS counterpart so that the registration
factory can be a near drop-in.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

from orb.domain.base.operation_outcome import OperationOutcome
from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.infrastructure.di.injectable import injectable
from orb.infrastructure.logging.logger import get_logger
from orb.providers.base.strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.reconciliation.orphan_gc import OrphanGarbageCollector
from orb.providers.k8s.reconciliation.startup_reconciler import (
    ReconciliationReport,
    StartupReconciler,
)
from orb.providers.k8s.services.capability_service import K8sCapabilityService
from orb.providers.k8s.services.health_check_service import K8sHealthCheckService
from orb.providers.k8s.services.infrastructure_discovery_service import (
    K8sInfrastructureDiscoveryService,
)
from orb.providers.k8s.services.instance_operation_service import (
    CancelResourceResult,
    K8sInstanceOperationService,
)
from orb.providers.k8s.services.start_stop_service import K8sStartStopService
from orb.providers.k8s.services.template_validation_service import K8sTemplateValidationService
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry
from orb.providers.k8s.strategy.native_spec_resolver import K8sNativeSpecResolver
from orb.providers.k8s.strategy.node_watch_lifecycle import K8sNodeWatchLifecycle
from orb.providers.k8s.strategy.outcome_bridge import (
    _all_instances_terminal,
    _build_provider_result_data,
    _outcome_to_provider_result,
)
from orb.providers.k8s.strategy.reconciliation_lifecycle import K8sReconciliationLifecycle
from orb.providers.k8s.strategy.watch_lifecycle import K8sWatchManagerLifecycle
from orb.providers.k8s.value_objects import KubernetesProviderApi
from orb.providers.k8s.watch.events_watcher import K8sEventsWatcher, K8sNodeEventsCache
from orb.providers.k8s.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.k8s.watch.node_state_cache import K8sNodeStateCache
from orb.providers.k8s.watch.node_watcher import K8sNodeWatcher
from orb.providers.k8s.watch.pod_state_cache import PodStateCache

_logger = get_logger(__name__)

# Re-exported for backward compatibility: tests and callers import these
# bridge helpers from this module.  The implementations now live in
# ``outcome_bridge`` to keep the strategy shell focused on lifecycle.
__all__ = [
    "K8sProviderStrategy",
    "_all_instances_terminal",
    "_build_provider_result_data",
    "_outcome_to_provider_result",
]

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.request.aggregate import Request
    from orb.domain.template.template_aggregate import Template
    from orb.monitoring.health import HealthCheck


@injectable
class K8sProviderStrategy(ProviderStrategy):
    """Kubernetes implementation of the :class:`ProviderStrategy` interface.

    Wires the strategy shell — config validation, lazy K8sClient
    construction, health check, capabilities — and delegates the typed
    ``acquire`` / ``return_machines`` / ``get_status`` operations to the
    per-provider-API handlers via :class:`K8sHandlerRegistry`.
    """

    _SUPPORTED_APIS: tuple[str, ...] = tuple(api.value for api in KubernetesProviderApi)

    # Canonical aliases for lowercase (or alternate-case) provider_api values.
    # REST or CLI submissions that spell ``provider_api="pod"`` (lowercase) are
    # normalised to ``"Pod"`` before reaching the handler registry, preventing
    # opaque ``NotImplementedError`` failures.  Extend this dict to cover any
    # additional aliases as new workload kinds are introduced.
    _API_ALIASES: dict[str, str] = {
        "pod": "Pod",
        "deployment": "Deployment",
        "statefulset": "StatefulSet",
        "job": "Job",
    }

    # Class-level frozen seed for plugin factories — used only to seed the
    # per-instance dict created in ``__init__``.  Never mutate this dict
    # directly; use :meth:`register_handler` which operates on the instance.
    _DEFAULT_HANDLER_FACTORIES: dict[str, Callable[..., K8sHandlerBase]] = {}

    def register_handler(
        self,
        provider_api: str,
        handler_class: Callable[..., K8sHandlerBase],
    ) -> None:
        """Register a plugin handler factory against a ``provider_api`` key.

        The factory must accept the full seven-kwarg surface:
        ``kubernetes_client``, ``config``, ``logger``, ``pod_state_cache``,
        ``cache_alive``, ``native_spec_service``, and ``node_state_cache``.
        Plugin authors typically subclass
        :class:`orb.providers.k8s.handlers.base_handler.K8sHandlerBase`
        which already accepts those kwargs.

        Registration is scoped to this strategy instance — two strategy
        objects for different clusters do not share plugin state.

        Args:
            provider_api: The ``provider_api`` template field this handler
                will service (e.g. ``"KubernetesMPIJob"``).
            handler_class: A callable that returns a configured handler
                instance — usually a subclass of ``K8sHandlerBase``.

        Raises:
            ValueError: If ``provider_api`` is already registered to a
                different handler class.  Idempotent re-registration of
                the same class is allowed so that plugin reloads do not
                fail.
        """
        existing = self._handler_factories.get(provider_api)
        if existing is not None and existing is not handler_class:
            raise ValueError(
                f"provider_api {provider_api!r} is already registered to a "
                f"different handler class ({existing!r}); refusing to overwrite."
            )
        self._handler_factories[provider_api] = handler_class

    def unregister_handler(self, provider_api: str) -> None:
        """Remove a plugin-registered handler from this instance (for tests / reload)."""
        self._handler_factories.pop(provider_api, None)

    def __init__(
        self,
        config: K8sProviderConfig,
        logger: LoggingPort,
        provider_name: Optional[str] = None,
        provider_instance_config: Optional[Any] = None,
        config_port: Optional[ConfigurationPort] = None,
        console: Optional[Any] = None,
        kubernetes_client: Optional[K8sClient] = None,
        handler_overrides: Optional[dict[str, K8sHandlerBase]] = None,
        watch_manager: Optional[MultiNamespaceWatcher] = None,
        known_request_ids: Optional[Callable[[], Iterable[str]]] = None,
        startup_reconciler: Optional[StartupReconciler] = None,
        orphan_gc: Optional[OrphanGarbageCollector] = None,
        node_watcher: Optional[K8sNodeWatcher] = None,
        node_state_cache: Optional[K8sNodeStateCache] = None,
        events_watcher: Optional[K8sEventsWatcher] = None,
        node_events_cache: Optional[K8sNodeEventsCache] = None,
        native_spec_service: Optional[Any] = None,
    ) -> None:
        if not isinstance(config, K8sProviderConfig):
            raise ValueError("K8sProviderStrategy requires K8sProviderConfig")

        super().__init__(config)
        self._logger = logger
        self._k8s_config = config
        self._console = console
        self._provider_instance_config = provider_instance_config
        self._provider_name = provider_name
        self._config_port = config_port
        self._kubernetes_client: Optional[K8sClient] = kubernetes_client
        # ``known_request_ids`` is the storage closure the strategy hands
        # to the reconciler + orphan GC — when the caller does not supply
        # it the reconciler treats every managed pod as an orphan (safest
        # signal) and the GC is wired to an empty set.
        self._known_request_ids_fn: Callable[[], Iterable[str]] = known_request_ids or (lambda: ())
        # ------------------------------------------------------------------
        # Lifecycle sub-services — mirror the AWS provider's delegation
        # layout.  Each owns one cohesive background subsystem so the
        # strategy shell stays a thin orchestrator.
        # ------------------------------------------------------------------
        # Watch fan-out (+ shared PodStateCache + metrics recorder).
        self._watch_lifecycle = K8sWatchManagerLifecycle(
            config=self._k8s_config,
            logger=self._logger,
            client_provider=lambda: self.kubernetes_client,
            watch_manager=watch_manager,
        )
        # Startup reconciler + orphan GC.  Tests can override both
        # subsystems wholesale via the constructor kwargs.
        self._reconciliation_lifecycle = K8sReconciliationLifecycle(
            config=self._k8s_config,
            logger=self._logger,
            client_provider=lambda: self.kubernetes_client,
            watch_lifecycle=self._watch_lifecycle,
            known_request_ids=self._known_request_ids_fn,
            startup_reconciler=startup_reconciler,
            orphan_gc=orphan_gc,
        )
        # Node watcher + events watcher (+ their shared caches).  Tests
        # inject watchers/caches via constructor kwargs to avoid real
        # threads.
        self._node_watch_lifecycle = K8sNodeWatchLifecycle(
            config=self._k8s_config,
            logger=self._logger,
            client_provider=lambda: self.kubernetes_client,
            node_state_cache=node_state_cache,
            node_watcher=node_watcher,
            node_events_cache=node_events_cache,
            events_watcher=events_watcher,
        )
        # Native-spec escape hatch.  Resolved lazily on first handler
        # construction; owns the DI-container / config-port plumbing.
        self._native_spec_resolver = K8sNativeSpecResolver(
            config=self._k8s_config,
            logger=self._logger,
            config_port=self._config_port,
            injected_native_spec_service=native_spec_service,
        )
        # Infrastructure discovery service — constructed lazily by
        # :meth:`_get_discovery_service` on first use.
        self._discovery_service: Optional[K8sInfrastructureDiscoveryService] = None
        # Focused service objects — mirror the AWS provider's layout.
        self._capability_service = K8sCapabilityService(logger=self._logger)
        self._template_service = K8sTemplateValidationService(logger=self._logger)
        self._health_check_service = K8sHealthCheckService(
            config=self._k8s_config,
            logger=self._logger,
        )
        self._instance_operation_service = K8sInstanceOperationService(
            config=self._k8s_config,
            logger=self._logger,
        )
        # Start/stop service (scale Deployment/StatefulSet) — constructed
        # lazily by :meth:`_get_start_stop_service` on first use so the
        # kubernetes client is resolved only when a start/stop is requested.
        self._start_stop_service: Optional[K8sStartStopService] = None
        # Per-instance plugin factory registry — seeded from the class-level
        # defaults so every instance starts with the same empty set but is
        # fully isolated from other instances.  Two strategy objects for
        # different clusters in the same process do not share plugin state.
        self._handler_factories: dict[str, Callable[..., K8sHandlerBase]] = dict(
            type(self)._DEFAULT_HANDLER_FACTORIES
        )
        # Guard that ensures :meth:`start_daemon_services` is idempotent.
        # Set to True only after all four sub-systems start successfully;
        # a second invocation (e.g. uvicorn worker recycle) short-circuits
        # immediately to prevent double-reconciliation.
        self._daemon_services_started: bool = False
        # Handler registry — does the per-API handler factory wiring and
        # the typed acquire/return/status dispatch.  Wired with closures
        # over the strategy's lazy client, watcher, native-spec accessors
        # and the per-instance plugin factory dict so the registry never
        # re-implements those lifecycles.
        self._handler_registry = K8sHandlerRegistry(
            config=self._k8s_config,
            logger=self._logger,
            client_provider=lambda: self.kubernetes_client,
            watch_manager_provider=lambda: self._watch_lifecycle.watch_manager,
            plugin_factories=lambda: self._handler_factories,
            native_spec_service_provider=self._resolve_native_spec_service,
            handler_overrides=handler_overrides,
            node_state_cache_provider=lambda: self._node_watch_lifecycle.node_state_cache,
            api_aliases=type(self)._API_ALIASES,
            metrics_provider=self._get_metrics,
        )

    # ------------------------------------------------------------------
    # Provider identity
    # ------------------------------------------------------------------

    @property
    def provider_type(self) -> str:
        return "k8s"

    @property
    def provider_name(self) -> Optional[str]:
        return self._provider_name

    @property
    def kubernetes_client(self) -> K8sClient:
        """Lazy ``K8sClient`` accessor.

        Constructs the client on first access using the validated provider
        config and the injected logger.  Unit tests can pre-supply a mock
        client via the ``kubernetes_client`` constructor argument.
        """
        if self._kubernetes_client is None:
            self._kubernetes_client = K8sClient(
                config=self._k8s_config,
                logger=self._logger,
            )
        return self._kubernetes_client

    @property
    def _handlers(self) -> dict[str, K8sHandlerBase]:
        """Handler cache view — preserved for test fixtures that pre-seed it."""
        return self._handler_registry.handlers

    # ------------------------------------------------------------------
    # Sub-service state delegation
    #
    # The background subsystems now live in dedicated lifecycle services,
    # but the strategy's private attributes remain part of the contract
    # relied on by fixtures and integration tests (which pre-seed / patch
    # them directly).  These properties keep that surface intact while the
    # implementation lives in the extracted services.
    # ------------------------------------------------------------------

    @property
    def _watch_manager(self) -> Optional[MultiNamespaceWatcher]:
        return self._watch_lifecycle.watch_manager

    @_watch_manager.setter
    def _watch_manager(self, value: Optional[MultiNamespaceWatcher]) -> None:
        self._watch_lifecycle.watch_manager = value

    @property
    def _orphan_gc(self) -> Optional[OrphanGarbageCollector]:
        return self._reconciliation_lifecycle.orphan_gc

    @_orphan_gc.setter
    def _orphan_gc(self, value: Optional[OrphanGarbageCollector]) -> None:
        self._reconciliation_lifecycle.orphan_gc = value

    @property
    def _startup_reconciler(self) -> Optional[StartupReconciler]:
        return self._reconciliation_lifecycle.startup_reconciler

    @_startup_reconciler.setter
    def _startup_reconciler(self, value: Optional[StartupReconciler]) -> None:
        self._reconciliation_lifecycle.startup_reconciler = value

    @property
    def _node_watcher(self) -> Optional[K8sNodeWatcher]:
        return self._node_watch_lifecycle.node_watcher

    @_node_watcher.setter
    def _node_watcher(self, value: Optional[K8sNodeWatcher]) -> None:
        self._node_watch_lifecycle.node_watcher = value

    @property
    def _events_watcher(self) -> Optional[K8sEventsWatcher]:
        return self._node_watch_lifecycle.events_watcher

    @_events_watcher.setter
    def _events_watcher(self, value: Optional[K8sEventsWatcher]) -> None:
        self._node_watch_lifecycle.events_watcher = value

    @property
    def _node_state_cache(self) -> K8sNodeStateCache:
        return self._node_watch_lifecycle.node_state_cache

    @property
    def _node_events_cache(self) -> K8sNodeEventsCache:
        return self._node_watch_lifecycle.node_events_cache

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

    async def start_daemon_services(self) -> None:
        """Start the watch fleet, startup reconciler, orphan GC and node watcher.

        Called by the REST/daemon entrypoint after ``Application.initialize``
        completes and the asyncio event loop is running.  Not called by the
        CLI: CLI commands are one-shot and have no use for a warmed cache or
        background watchers, and the synchronous ``list_pods`` issued by the
        reconciler would otherwise block every command on apiserver latency.

        This method is idempotent: a second call (e.g. from a uvicorn worker
        recycle) short-circuits immediately without re-running reconciliation.
        Re-running reconciliation against an already-running provider can
        misclassify pods that were created between the two invocations as
        orphans, so the guard is intentional.  ``_daemon_services_started`` is
        set to ``True`` only after all four sub-systems complete successfully;
        if startup raises, a subsequent call will retry.

        Each sub-step tolerates failure — errors are logged and the provider
        continues to serve reads via the cache-less fallback path.
        """
        if self._daemon_services_started:
            self._logger.debug(
                "Kubernetes daemon services already started; skipping second invocation."
            )
            return
        await self._run_startup_reconciler()
        self._maybe_start_watch_manager()
        self._maybe_start_orphan_gc()
        self._maybe_start_node_watcher()
        self._maybe_start_events_watcher()
        self._daemon_services_started = True

    def cleanup(self) -> None:
        # Each stage is wrapped independently so that a failure in an
        # earlier stage does not prevent later stages from running.
        # In particular, an ApiClient connection-pool leak from an
        # orphan-GC stop failure is avoided by always reaching the
        # client cleanup stage.
        try:
            if self._orphan_gc is not None:
                self._stop_orphan_gc_sync()
        except Exception as exc:
            self._logger.warning(
                "Failed to stop Kubernetes orphan GC during cleanup: %s", exc, exc_info=True
            )

        try:
            if self._watch_manager is not None:
                # ``stop`` is async; schedule it on the running loop if
                # there is one, otherwise drive it synchronously via
                # ``asyncio.run``.  CLI cleanup paths typically have no
                # loop running while daemon paths do.
                self._stop_watch_manager_sync()
        except Exception as exc:
            self._logger.warning(
                "Failed to stop Kubernetes watch manager during cleanup: %s", exc, exc_info=True
            )

        try:
            self._node_watch_lifecycle.stop_node_watcher()
        except Exception as exc:
            self._logger.warning(
                "Failed to stop Kubernetes node watcher during cleanup: %s", exc, exc_info=True
            )

        try:
            self._node_watch_lifecycle.stop_events_watcher()
        except Exception as exc:
            self._logger.warning(
                "Failed to stop Kubernetes events watcher during cleanup: %s", exc, exc_info=True
            )

        # Stage 4: client cleanup.  Only clear ``_initialized`` when the
        # client is successfully cleaned up — if this stage fails the
        # provider can still serve reads via the existing connection while
        # the operator investigates, and ``check_health()`` will surface
        # the degraded state.  If the client was never created (None), the
        # provider is still considered cleanly shut down.
        _client_cleanup_succeeded = False
        try:
            if self._kubernetes_client is not None:
                self._kubernetes_client.cleanup()
            self._kubernetes_client = None
            _client_cleanup_succeeded = True
        except Exception as exc:
            self._logger.warning(
                "Failed to clean up Kubernetes client during cleanup: %s", exc, exc_info=True
            )

        if _client_cleanup_succeeded:
            self._initialized = False

    def _ensure_watch_manager(self) -> MultiNamespaceWatcher:
        """Lazily construct (but do NOT start) the watch fan-out.

        Delegates to :class:`K8sWatchManagerLifecycle`.  Exposed so the
        startup reconciler can share the watcher's :class:`PodStateCache`.
        """
        return self._watch_lifecycle.ensure()

    def _maybe_start_watch_manager(self) -> None:
        """Start the watch fleet when enabled by config and a loop is available."""
        self._watch_lifecycle.maybe_start()

    def _stop_watch_manager_sync(self, *, shutdown_timeout: float = 10.0) -> None:
        """Stop the watch manager, blocking until all watchers exit or the timeout elapses.

        Delegates to :class:`K8sWatchManagerLifecycle`; see it for the
        three context-dependent shutdown paths.
        """
        self._watch_lifecycle.stop_sync(shutdown_timeout=shutdown_timeout)

    # ------------------------------------------------------------------
    # Reconciliation / orphan-GC lifecycle
    # ------------------------------------------------------------------

    @property
    def last_reconciliation_report(self) -> Optional[ReconciliationReport]:
        """Surface the most recent :class:`ReconciliationReport` for diagnostics."""
        return self._reconciliation_lifecycle.last_reconciliation_report

    def _get_metrics(self) -> Optional[Any]:
        """Return the shared :class:`K8sMetrics` instance, constructing on demand.

        Delegates to :class:`K8sWatchManagerLifecycle` which owns the
        recorder so watcher and handlers share the same OTel meter.
        """
        return self._watch_lifecycle.get_metrics()

    def _shared_cache(self) -> PodStateCache:
        """Return the cache used by both reconciler and watcher."""
        return self._watch_lifecycle.shared_cache()

    async def _run_startup_reconciler(self) -> None:
        """Run the startup reconciler before the watch task spawns.

        Delegates to :class:`K8sReconciliationLifecycle`.  Only called from
        :meth:`start_daemon_services` — the CLI path never reaches this
        method.
        """
        await self._reconciliation_lifecycle.run_startup_reconciler()

    def _maybe_start_orphan_gc(self) -> None:
        """Spawn the orphan GC task when enabled and an event loop is available."""
        self._reconciliation_lifecycle.maybe_start_orphan_gc()

    def _stop_orphan_gc_sync(self, *, stop_timeout: float = 5.0) -> None:
        """Stop the orphan GC from a sync-or-async cleanup context.

        Delegates to :class:`K8sReconciliationLifecycle`; see it for the
        three context-dependent shutdown paths.
        """
        self._reconciliation_lifecycle.stop_orphan_gc_sync(stop_timeout=stop_timeout)

    # ------------------------------------------------------------------
    # Node watcher lifecycle
    # ------------------------------------------------------------------

    @property
    def node_state_cache(self) -> K8sNodeStateCache:
        """The shared node-state cache used to enrich per-instance status dicts.

        Always present (never ``None``) — when ``node_watch_enabled`` is
        ``False`` it is simply an empty cache that returns ``None`` for
        every lookup.
        """
        return self._node_watch_lifecycle.node_state_cache

    def _maybe_start_node_watcher(self) -> None:
        """Start the node watcher when enabled by config (delegated)."""
        self._node_watch_lifecycle.maybe_start_node_watcher()

    # ------------------------------------------------------------------
    # Events watcher lifecycle
    # ------------------------------------------------------------------

    @property
    def node_events_cache(self) -> K8sNodeEventsCache:
        """The shared node-events cache populated by the Events API watcher.

        Always present (never ``None``) -- when ``events_watch_enabled`` is
        ``False`` it is simply an empty cache that returns ``None`` for
        every lookup.
        """
        return self._node_watch_lifecycle.node_events_cache

    def _maybe_start_events_watcher(self) -> None:
        """Start the Events API watcher when enabled by config (delegated)."""
        self._node_watch_lifecycle.maybe_start_events_watcher()

    # ------------------------------------------------------------------
    # Operation dispatch
    # ------------------------------------------------------------------

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """Execute a provider operation.

        Dispatches the shared :class:`ProviderOperation` envelope to the
        kubernetes provider's typed entry points.  ``CREATE_INSTANCES``,
        ``TERMINATE_INSTANCES`` and ``GET_INSTANCE_STATUS`` map to
        :meth:`acquire`, :meth:`return_machines` and :meth:`get_status`
        respectively; ``HEALTH_CHECK`` is serviced inline.  Other
        operation types return ``UNSUPPORTED_OPERATION``.

        The shared call site (``ProvisioningOrchestrationService``) puts
        the live :class:`Request` and :class:`Template` aggregates into
        ``operation.parameters``; AWS ignores them.  When they are
        absent (older callers) the strategy raises an explicit error
        rather than fabricate a request silently.
        """
        self._logger.debug("Kubernetes strategy executing operation: %s", operation.operation_type)

        if not self._initialized:
            return ProviderResult.error_result(
                "Kubernetes provider strategy not initialized", "NOT_INITIALIZED"
            )

        dry_run = bool(operation.context.get("dry_run", False)) if operation.context else False
        if dry_run:
            self._logger.info(
                "Kubernetes strategy: dry-run requested for operation %s — returning synthetic "
                "success without contacting the cluster.",
                operation.operation_type,
            )
            return ProviderResult.success_result(
                {
                    "resource_ids": [],
                    "instances": [],
                    "instance_ids": [],
                    "provider_data": {"dry_run": True},
                },
                {
                    "operation": str(operation.operation_type),
                    "provider": "k8s",
                    "fulfillment_final": True,
                },
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
            elif operation.operation_type == ProviderOperationType.CREATE_INSTANCES:
                result = await self._handle_create_instances(operation)
            elif operation.operation_type == ProviderOperationType.TERMINATE_INSTANCES and (
                operation.context or {}
            ).get("cancel_mode"):
                # cancel_mode: in-flight cancel before pods exist — delete
                # workloads by orb.io/request-id label rather than by pod name.
                # This branch must precede the plain TERMINATE_INSTANCES branch
                # so that cancel-path operations are not silently routed to the
                # normal deprovisioning handler.
                result = await self._handle_cancel_resource(operation)
            elif operation.operation_type == ProviderOperationType.TERMINATE_INSTANCES:
                result = await self._handle_terminate_instances(operation)
            elif operation.operation_type == ProviderOperationType.GET_INSTANCE_STATUS:
                result = await self._handle_get_instance_status(operation)
            elif operation.operation_type == ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES:
                result = await self._handle_describe_resource_instances(operation)
            elif operation.operation_type == ProviderOperationType.VALIDATE_TEMPLATE:
                result = self._template_service.validate_template(operation)
            elif operation.operation_type == ProviderOperationType.START_INSTANCES:
                result = await self._get_start_stop_service().start_instances(operation)
            elif operation.operation_type == ProviderOperationType.STOP_INSTANCES:
                result = await self._get_start_stop_service().stop_instances(operation)
            else:
                result = ProviderResult.error_result(
                    f"Operation {operation.operation_type} is not supported by the "
                    "kubernetes provider.",
                    "UNSUPPORTED_OPERATION",
                )

            execution_time_ms = int((time.time() - start_time) * 1000)
            return result.model_copy(
                update={
                    "routing_info": {
                        "execution_time_ms": execution_time_ms,
                        "provider": "k8s",
                    },
                    "metadata": {
                        **result.metadata,
                        "execution_time_ms": execution_time_ms,
                        "provider": "k8s",
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
                        "provider": "k8s",
                    }
                }
            )

    # ------------------------------------------------------------------
    # Shared-envelope -> typed-interface bridges
    # ------------------------------------------------------------------

    async def _handle_create_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Dispatch a ``CREATE_INSTANCES`` operation to the typed :meth:`acquire`.

        Threads the live :class:`Template` from ``operation.parameters`` into
        ``request.metadata['template']`` where ``K8sHandlerRegistry.build_
        template_for_request`` looks for it.  The provisioning service does
        not put the template on the request itself today; doing so here keeps
        the change local to the kubernetes bridge instead of mutating shared
        request-creation code.
        """
        request = operation.parameters.get("request")
        if request is None:
            return ProviderResult.error_result(
                "CREATE_INSTANCES requires the typed 'request' object in "
                "operation.parameters for the kubernetes provider.",
                "MISSING_REQUEST",
            )
        template = operation.parameters.get("template")
        if template is not None:
            existing_meta = dict(getattr(request, "metadata", None) or {})
            if "template" not in existing_meta:
                existing_meta["template"] = template
                request = request.update_metadata(existing_meta)
        outcome = await self.acquire(request)
        return _outcome_to_provider_result(outcome, fallback_operation="create_instances")

    async def _handle_terminate_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Dispatch a ``TERMINATE_INSTANCES`` operation to :meth:`return_machines`."""
        request = operation.parameters.get("request")
        if request is None:
            return ProviderResult.error_result(
                "TERMINATE_INSTANCES requires the typed 'request' object in "
                "operation.parameters for the kubernetes provider.",
                "MISSING_REQUEST",
            )
        machine_ids = list(
            operation.parameters.get("instance_ids")
            or operation.parameters.get("machine_ids")
            or []
        )
        # A return request carries its own request_id and no controller name, so
        # the controller-backed handlers (Deployment/StatefulSet/Job) would
        # otherwise resolve the wrong resource name and no-op.  The deprovisioning
        # operation supplies the acquire-time controller name (resource_id) and
        # the origin request_id — thread both into provider_data so release
        # targets the resource that was actually created.  The name key is set
        # for every controller kind; each handler reads only its own key, and the
        # Pod handler ignores all of them (it deletes by machine_ids).
        overrides: dict[str, Any] = {}
        resource_id = operation.parameters.get("resource_id")
        if resource_id:
            overrides["deployment_name"] = resource_id
            overrides["statefulset_name"] = resource_id
            overrides["job_name"] = resource_id
        origin_request_id = operation.parameters.get("request_id")
        if origin_request_id:
            overrides["request_id"] = str(origin_request_id)
        outcome = await self.return_machines(
            machine_ids, request, provider_data_overrides=overrides or None
        )
        return _outcome_to_provider_result(outcome, fallback_operation="terminate_instances")

    async def _handle_get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        """Dispatch a ``GET_INSTANCE_STATUS`` operation to :meth:`get_status`."""
        request = operation.parameters.get("request")
        if request is None:
            return ProviderResult.error_result(
                "GET_INSTANCE_STATUS requires the typed 'request' object in "
                "operation.parameters for the kubernetes provider.",
                "MISSING_REQUEST",
            )
        resource_ids = list(
            operation.parameters.get("resource_ids")
            or operation.parameters.get("instance_ids")
            or []
        )
        outcome = await self.get_status(resource_ids, request)
        return _outcome_to_provider_result(outcome, fallback_operation="get_instance_status")

    async def _handle_describe_resource_instances(
        self, operation: ProviderOperation
    ) -> ProviderResult:
        """Dispatch ``DESCRIBE_RESOURCE_INSTANCES`` to the per-API status handler.

        Status-polling code paths use this operation type to interrogate a
        specific set of resource IDs without the full acquire/return lifecycle.
        The call is forwarded to :meth:`get_status` — the handler returns live
        pod state via ``check_hosts_status`` and the fulfilment value is
        surfaced as ``provider_fulfilment`` in metadata so callers can
        distinguish in-progress from terminal states.
        """
        request = operation.parameters.get("request")
        if request is None:
            return ProviderResult.error_result(
                "DESCRIBE_RESOURCE_INSTANCES requires the typed 'request' object in "
                "operation.parameters for the kubernetes provider.",
                "MISSING_REQUEST",
            )
        resource_ids = list(
            operation.parameters.get("resource_ids")
            or operation.parameters.get("instance_ids")
            or []
        )
        outcome = await self.get_status(resource_ids, request)
        result = _outcome_to_provider_result(
            outcome, fallback_operation="describe_resource_instances"
        )
        # Surface the fulfilment object in metadata so callers can inspect
        # ``.state`` / ``.message`` / ``.running_count`` etc. without
        # digging into provider_data.  The object is stored as-is — storing
        # only the state string would break every consumer that calls
        # ``.state`` or ``.message`` on the field.
        from orb.domain.base.operation_outcome import Accepted, Completed

        if isinstance(outcome, (Accepted, Completed)):
            fulfilment = (outcome.metadata or {}).get("fulfilment")
            if fulfilment is not None:
                result = result.model_copy(
                    update={
                        "metadata": {
                            **result.metadata,
                            "provider_fulfilment": fulfilment,
                        }
                    }
                )
        return result

    async def _handle_cancel_resource(self, operation: ProviderOperation) -> ProviderResult:
        """Delete in-flight workloads by ``orb.io/request-id`` label.

        Called via ``execute_operation`` when ``operation.context["cancel_mode"]``
        is truthy.  Delegates to :class:`K8sInstanceOperationService.cancel_resource`
        which finds every Pod / Deployment / StatefulSet / Job carrying the
        request-id label and deletes them.
        """
        request_id = operation.parameters.get("request_id") or (
            str(
                getattr(
                    operation.parameters.get("request"),
                    "request_id",
                    "",
                )
            )
        )
        if not request_id:
            return ProviderResult.error_result(
                "cancel_resource requires request_id in operation.parameters",
                "MISSING_REQUEST_ID",
            )
        result = await self._instance_operation_service.cancel_resource(
            request_id=request_id,
            kubernetes_client=self.kubernetes_client,
        )
        if result.status == "partial":
            return ProviderResult.error_result(
                f"cancel_resource partially failed for request {request_id}: "
                f"{[f for f in result.failed]}",
                "PARTIAL_CANCEL_FAILURE",
            ).model_copy(
                update={"data": result.to_dict(), "success": True}  # surface partial data
            )
        return ProviderResult.success_result(
            result.to_dict(),
            {"operation": "cancel_resource", "provider": "k8s"},
        )

    async def cancel_resource(self, request_id: str) -> CancelResourceResult:
        """Delete all workloads associated with *request_id*.

        Public entry point for the cancel path.  Delegates to
        :class:`K8sInstanceOperationService.cancel_resource` so tests and
        callers that hold a strategy reference can invoke the operation
        directly without constructing a :class:`ProviderOperation` envelope.

        Args:
            request_id: The ORB request UUID to cancel.

        Returns:
            :class:`CancelResourceResult` with per-kind delete outcomes.
        """
        return await self._instance_operation_service.cancel_resource(
            request_id=request_id,
            kubernetes_client=self.kubernetes_client,
        )

    # ------------------------------------------------------------------
    # Capabilities & health
    # ------------------------------------------------------------------

    @classmethod
    def is_image_resolution_needed(cls) -> bool:
        """Kubernetes does not resolve image references provider-side.

        Container images are pulled by the kubelet at pod start from the image
        string as-is; there is no SSM-style indirection to resolve.  Declaring
        this explicitly stops the TemplateConfigurationManager from attempting
        (and warning about) image resolution against the k8s strategy.
        """
        return False

    def get_capabilities(self) -> ProviderCapabilities:
        return self._capability_service.get_capabilities()

    def check_health(self) -> ProviderHealthStatus:
        """Probe the Kubernetes API server via ``CoreV1Api.get_api_resources``.

        Delegates to :class:`K8sHealthCheckService` which houses all the
        enrichment and probe logic.
        """
        return self._health_check_service.check_health(self.kubernetes_client)

    # ------------------------------------------------------------------
    # Naming
    # ------------------------------------------------------------------

    @classmethod
    def generate_provider_name(cls, config: dict[str, Any]) -> str:
        """Generate a Kubernetes provider instance name.

        Delegates to :class:`K8sCapabilityService`.  See that class for the
        full specification of the ``k8s_{sanitized_context}`` pattern.
        """
        return K8sCapabilityService.generate_provider_name(config)

    @classmethod
    def get_defaults_config(cls) -> dict:
        """Return the k8s provider defaults configuration.

        Mirrors :meth:`AWSProviderStrategy.get_defaults_config`.  Loads the
        bundled ``k8s_defaults.json`` via :mod:`importlib.resources` so the
        file is found regardless of the installation method (editable install,
        wheel, zipimport).  The returned dict is validated by constructing a
        :class:`K8sProviderConfig` from the ``provider.provider_defaults.k8s``
        block so schema drift is caught early.
        """
        import json
        from importlib.resources import files

        from orb.providers.k8s.configuration.config import K8sProviderConfig

        text = (
            files("orb.providers.k8s.config")
            .joinpath("k8s_defaults.json")
            .read_text(encoding="utf-8")
        )
        raw = json.loads(text)
        provider_config = raw.get("provider", {}).get("provider_defaults", {}).get("k8s", {})
        # Lightweight structural validation — raises ValidationError on schema drift.
        if provider_config:
            K8sProviderConfig(
                **{
                    k: v
                    for k, v in provider_config.items()
                    if k not in ("handlers", "template_defaults")
                }
            )
        return raw

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        """Inverse of :meth:`generate_provider_name`."""
        return K8sCapabilityService.parse_provider_name(provider_name)

    def get_provider_name_pattern(self) -> str:
        return K8sCapabilityService.get_provider_name_pattern()

    def get_supported_apis(self) -> list[str]:
        return K8sCapabilityService.get_supported_apis()

    def resolve_api_alias(self, raw_api: str) -> str:
        """Normalise alternate-case provider_api spellings to canonical form.

        Consults :attr:`_API_ALIASES` first so that submissions with
        ``provider_api="pod"`` (lowercase) resolve to ``"Pod"`` rather than
        raising an opaque ``NotImplementedError`` from the handler registry.
        Unknown values are returned unchanged.
        """
        return self._API_ALIASES.get(raw_api, raw_api)

    @classmethod
    def get_ui_column_schema(cls) -> list:  # type: ignore[override]
        """Return k8s-specific UI column descriptors for machines, requests, and templates.

        Delegates to :class:`K8sCapabilityService` which owns the descriptor
        catalogue alongside the other capability/naming declarations.
        """
        return K8sCapabilityService.get_ui_column_schema()

    # ------------------------------------------------------------------
    # Region / CLI helpers
    # ------------------------------------------------------------------

    @classmethod
    def get_available_regions(cls) -> list[tuple[str, str]]:
        """Kubernetes has contexts, not regions — return an empty list."""
        return K8sCapabilityService.get_available_regions()

    @classmethod
    def get_default_region(cls) -> str:
        """Kubernetes has no region concept; return an empty string."""
        return K8sCapabilityService.get_default_region()

    @classmethod
    def get_cli_extra_config_keys(cls) -> set[str]:
        """Return k8s keys that belong in provider config, not template_defaults."""
        return K8sCapabilityService.get_cli_extra_config_keys()

    @classmethod
    def get_cli_infrastructure_defaults(cls, args: Any) -> dict[str, Any]:
        """Extract k8s infrastructure defaults from parsed CLI args."""
        return K8sCapabilityService.get_cli_infrastructure_defaults(args)

    @classmethod
    def get_cli_provider_config(cls, args: Any) -> dict[str, Any]:
        """Extract Kubernetes provider config keys from parsed CLI args."""
        return K8sCapabilityService.get_cli_provider_config(args)

    @classmethod
    def get_operational_param_choices(cls, param: str) -> list[tuple[str, str]]:
        """Return picker choices for an operational parameter, if any."""
        return K8sCapabilityService.get_operational_param_choices(param)

    @classmethod
    def get_operational_param_default(cls, param: str) -> str:
        """Return the default value for an operational parameter."""
        return K8sCapabilityService.get_operational_param_default(param)

    # ------------------------------------------------------------------
    # Credential surface (called by `orb init` and credential probes)
    # ------------------------------------------------------------------

    @classmethod
    def get_available_credential_sources(cls) -> list[dict]:
        """Return Kubernetes credential sources visible to ORB."""
        return K8sCapabilityService(logger=_logger).get_available_credential_sources()  # type: ignore[arg-type]

    @classmethod
    def test_credentials(cls, credential_source: Optional[str] = None, **kwargs: Any) -> dict:
        """Verify the selected credentials can reach the apiserver."""
        return K8sCapabilityService.test_credentials(credential_source, **kwargs)

    @classmethod
    def get_credential_requirements(cls) -> dict:
        """Document the Kubernetes credential parameters operators may set."""
        return K8sCapabilityService.get_credential_requirements()

    @classmethod
    def get_operational_requirements(cls) -> dict:
        """Document operational parameters the init flow may prompt for."""
        return K8sCapabilityService.get_operational_requirements()

    # ------------------------------------------------------------------
    # Health-check integration
    # ------------------------------------------------------------------

    def register_health_checks(self, health_check: HealthCheck) -> None:
        """Register Kubernetes-specific health checks if the client is reachable."""
        try:
            client = self.kubernetes_client
        except Exception as exc:
            self._logger.debug(
                "Skipping Kubernetes health-check registration: %s", exc, exc_info=True
            )
            return
        self._health_check_service.register_health_checks(health_check, client)

    # ------------------------------------------------------------------
    # Native-spec resolution — kept on the strategy because it owns the
    # DI container / config-port plumbing
    # ------------------------------------------------------------------

    def _resolve_native_spec_service(self) -> Optional[Any]:
        """Resolve :class:`K8sNativeSpecService` once on first handler build.

        Delegates to :class:`K8sNativeSpecResolver` which owns the
        config-port / injected-service gating.  Returns ``None`` when the
        provider config opts out, when no ``ConfigurationPort`` is wired,
        or when no ``native_spec_service`` was passed at construction time.
        """
        return self._native_spec_resolver.resolve()

    # ------------------------------------------------------------------
    # Handler dispatch — delegated to K8sHandlerRegistry
    # ------------------------------------------------------------------

    def _resolve_provider_api(self, request: Request) -> str:
        """Pick the provider-API key for ``request``."""
        return self._handler_registry.resolve_provider_api(request)

    def _get_handler(self, provider_api: str) -> K8sHandlerBase:
        """Return (and lazily construct) the handler for ``provider_api``."""
        return self._handler_registry.get_handler(provider_api)

    # ------------------------------------------------------------------
    # Typed provisioning interface
    # ------------------------------------------------------------------

    async def acquire(self, request: Request) -> OperationOutcome:
        """Submit an acquisition request to Kubernetes via the per-API handler."""
        return await self._handler_registry.acquire(request)

    async def return_machines(
        self,
        machine_ids: list[str],
        request: Request,
        provider_data_overrides: Optional[dict[str, Any]] = None,
    ) -> OperationOutcome:
        """Delete the named resources via the per-API handler."""
        return await self._handler_registry.return_machines(
            machine_ids, request, provider_data_overrides=provider_data_overrides
        )

    async def get_status(self, resource_ids: list[str], request: Request) -> OperationOutcome:
        """Poll the per-API handler's ``check_hosts_status`` for a verdict."""
        return await self._handler_registry.get_status(resource_ids, request)

    def _build_template_for_request(self, request: Request) -> Template:
        """Resolve the :class:`Template` carried by ``request``."""
        return self._handler_registry.build_template_for_request(request)

    # ------------------------------------------------------------------
    # Infrastructure discovery — ProviderDiscoveryPort implementation
    # ------------------------------------------------------------------

    def _get_discovery_service(self) -> K8sInfrastructureDiscoveryService:
        """Return the infrastructure discovery service, constructing it lazily.

        The discovery service is NOT registered in the DI container — the
        strategy owns and constructs it here, exactly as
        ``AWSProviderStrategy._get_infrastructure_service()`` does.  This keeps
        the DI container light and avoids requiring ``K8sProviderConfig`` (a
        per-strategy-instance value) to be resolvable from the container.
        """
        if self._discovery_service is None:
            self._discovery_service = K8sInfrastructureDiscoveryService(
                config=self._k8s_config,
                logger=self._logger,
                console=self._console,
            )
        return self._discovery_service

    def _get_start_stop_service(self) -> K8sStartStopService:
        """Return the start/stop service, constructing it lazily.

        Owns the ``START_INSTANCES`` / ``STOP_INSTANCES`` scale operations
        for Deployment/StatefulSet workloads.  Constructed on first use so
        the kubernetes client is resolved only when a start/stop is actually
        requested — mirrors the lazy discovery-service pattern above.
        """
        if self._start_stop_service is None:
            self._start_stop_service = K8sStartStopService(
                kubernetes_client=self.kubernetes_client,
                logger=self._logger,
            )
        return self._start_stop_service

    def discover_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover Kubernetes infrastructure for the configured cluster.

        Delegates to :class:`K8sInfrastructureDiscoveryService`.  Returns
        a valid discovery dict.
        """
        return self._get_discovery_service().discover_infrastructure(provider_config)

    def discover_infrastructure_interactive(
        self, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Interactively discover Kubernetes infrastructure via operator prompts.

        Delegates to :class:`K8sInfrastructureDiscoveryService`.  Returns
        the same scaffold as :meth:`discover_infrastructure`.
        """
        return self._get_discovery_service().discover_infrastructure_interactive(provider_config)

    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate that the configured Kubernetes cluster is reachable.

        Delegates to :class:`K8sInfrastructureDiscoveryService`.  Returns
        ``{provider, valid: True, issues: []}``.
        """
        return self._get_discovery_service().validate_infrastructure(provider_config)

    def __str__(self) -> str:  # pragma: no cover — trivial
        return (
            "K8sProviderStrategy("
            f"namespace={self._k8s_config.namespace}, "
            f"initialized={self._initialized})"
        )

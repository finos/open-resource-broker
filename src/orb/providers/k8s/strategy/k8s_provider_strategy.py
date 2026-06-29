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

import asyncio
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

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
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.reconciliation.orphan_gc import OrphanGarbageCollector
from orb.providers.k8s.reconciliation.startup_reconciler import (
    ReconciliationReport,
    StartupReconciler,
)
from orb.providers.k8s.services.infrastructure_discovery_service import (
    K8sInfrastructureDiscoveryService,
)
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry
from orb.providers.k8s.value_objects import KubernetesProviderApi
from orb.providers.k8s.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.k8s.watch.node_state_cache import K8sNodeStateCache
from orb.providers.k8s.watch.node_watcher import K8sNodeWatcher
from orb.providers.k8s.watch.pod_state_cache import PodStateCache

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

    # Plugin extension point — class-level registry of handler factories
    # keyed by ``provider_api`` value.  Third-party plugins call
    # :meth:`register_handler` from their ``orb.providers`` entry-point
    # callable to attach a handler to the Kubernetes provider without
    # forking the strategy.  See
    # ``docs/root/providers/k8s/plugin-authoring.md``.
    _HANDLER_FACTORIES: dict[str, Callable[..., K8sHandlerBase]] = {}

    @classmethod
    def register_handler(
        cls,
        provider_api: str,
        handler_class: Callable[..., K8sHandlerBase],
    ) -> None:
        """Register a handler class against a ``provider_api`` key.

        The ``handler_class`` must accept the standard handler kwargs:
        ``kubernetes_client``, ``config``, ``logger``, ``pod_state_cache``,
        and ``cache_alive``.  Plugin authors typically subclass
        :class:`orb.providers.k8s.handlers.base_handler.K8sHandlerBase`
        which already accepts those kwargs.

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
        existing = cls._HANDLER_FACTORIES.get(provider_api)
        if existing is not None and existing is not handler_class:
            raise ValueError(
                f"provider_api {provider_api!r} is already registered to a "
                f"different handler class ({existing!r}); refusing to overwrite."
            )
        cls._HANDLER_FACTORIES[provider_api] = handler_class

    @classmethod
    def unregister_handler(cls, provider_api: str) -> None:
        """Remove a plugin-registered handler (intended for tests / reload)."""
        cls._HANDLER_FACTORIES.pop(provider_api, None)

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
        # Watch fan-out.  Constructed lazily by :meth:`initialize` when
        # ``config.watch_enabled`` is True and no override has been
        # provided.  Tests inject a stub via ``watch_manager``.
        self._watch_manager: Optional[MultiNamespaceWatcher] = watch_manager
        # Reconciliation wiring: startup reconciler + orphan GC.
        # ``known_request_ids`` is the storage closure the strategy hands
        # to both — when the
        # caller does not supply it the reconciler treats every managed
        # pod as an orphan (safest signal) and the GC is wired to an
        # empty set.  Tests can override both subsystems wholesale.
        self._known_request_ids_fn: Callable[[], Iterable[str]] = known_request_ids or (lambda: ())
        self._startup_reconciler: Optional[StartupReconciler] = startup_reconciler
        self._orphan_gc: Optional[OrphanGarbageCollector] = orphan_gc
        self._last_reconciliation_report: Optional[ReconciliationReport] = None
        # Node watching.  When ``node_watch_enabled=True`` (opt-in via
        # K8sProviderConfig) the strategy starts a K8sNodeWatcher on the
        # background thread and exposes the populated K8sNodeStateCache to
        # handlers so per-instance status dicts carry node metadata.
        # Tests inject both via the constructor kwargs to avoid real threads.
        self._node_state_cache: K8sNodeStateCache = node_state_cache or K8sNodeStateCache()
        self._node_watcher: Optional[K8sNodeWatcher] = node_watcher
        # Native-spec escape hatch.  Resolved lazily on first handler
        # construction so tests / CLI bootstrap paths without a DI
        # container do not pay the resolution cost up front.  ``None``
        # after resolution means the service is unavailable (jinja2
        # missing, DI container empty, etc.) — handlers will fall back
        # to the typed builder path.
        self._native_spec_service_resolved: bool = False
        self._k8s_native_spec_service: Optional[Any] = None
        # Infrastructure discovery service — constructed lazily by
        # :meth:`_get_discovery_service` on first use.
        self._discovery_service: Optional[K8sInfrastructureDiscoveryService] = None
        # Handler registry — does the per-API handler factory wiring and
        # the typed acquire/return/status dispatch.  Wired with closures
        # over the strategy's lazy client, watcher, native-spec accessors
        # and the class-level plugin factory dict so the registry never
        # re-implements those lifecycles.
        self._handler_registry = K8sHandlerRegistry(
            config=self._k8s_config,
            logger=self._logger,
            client_provider=lambda: self.kubernetes_client,
            watch_manager_provider=lambda: self._watch_manager,
            plugin_factories=lambda: type(self)._HANDLER_FACTORIES,
            native_spec_service_provider=self._resolve_native_spec_service,
            handler_overrides=handler_overrides,
            node_state_cache_provider=lambda: self._node_state_cache,
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
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        try:
            self._logger.info(
                "Kubernetes provider strategy ready (namespace=%s, in_cluster=%s)",
                self._k8s_config.namespace,
                self._k8s_config.in_cluster,
            )
            # Startup reconciliation runs synchronously BEFORE the watch
            # task is spawned so the cache is warm by the time the first
            # ``check_hosts_status`` call lands.  Failures inside the
            # reconciler are logged and tolerated — the watcher will
            # converge on the correct state in steady-state.
            self._run_startup_reconciler()
            self._maybe_start_watch_manager()
            self._maybe_start_orphan_gc()
            self._maybe_start_node_watcher()
            self._initialized = True
            return True
        except Exception as exc:
            self._logger.error(
                "Failed to initialize Kubernetes provider strategy: %s", exc, exc_info=True
            )
            return False

    def cleanup(self) -> None:
        try:
            if self._orphan_gc is not None:
                self._stop_orphan_gc_sync()
            if self._watch_manager is not None:
                # ``stop`` is async; schedule it on the running loop if
                # there is one, otherwise drive it synchronously via
                # ``asyncio.run``.  CLI cleanup paths typically have no
                # loop running while daemon paths do.
                self._stop_watch_manager_sync()
            if self._node_watcher is not None:
                self._node_watcher.stop()
                self._node_watcher = None
            if self._kubernetes_client is not None:
                self._kubernetes_client.cleanup()
            self._kubernetes_client = None
            self._initialized = False
        except Exception as exc:
            self._logger.warning(
                "Failed during Kubernetes provider cleanup: %s", exc, exc_info=True
            )

    def _ensure_watch_manager(self) -> MultiNamespaceWatcher:
        """Lazily construct (but do NOT start) the watch fan-out.

        Exposed so the startup reconciler can share the watcher's
        :class:`PodStateCache`: the reconciler warms the cache before
        the watcher spawns, then the watcher takes over.  The watcher
        is only started later by :meth:`_maybe_start_watch_manager`
        when an event loop is available.
        """
        if self._watch_manager is None:
            self._watch_manager = MultiNamespaceWatcher(
                kubernetes_client=self.kubernetes_client,
                config=self._k8s_config,
                logger=self._logger,
            )
        return self._watch_manager

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
        manager = self._ensure_watch_manager()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._logger.debug(
                "Skipping Kubernetes watcher startup: no running event loop "
                "(cache-less fallback will serve reads)."
            )
            return
        try:
            manager.start()
        except Exception as exc:
            self._logger.warning("Failed to start Kubernetes watcher fleet: %s", exc, exc_info=True)

    def _stop_watch_manager_sync(self, *, shutdown_timeout: float = 10.0) -> None:
        """Stop the watch manager, blocking until all watchers exit or the timeout elapses.

        Three paths depending on the calling context:

        * **No running loop** — drives ``manager.stop()`` synchronously
          via :func:`asyncio.run`.
        * **Running loop, different thread** (e.g. signal handler) —
          schedules the coroutine via
          :func:`asyncio.run_coroutine_threadsafe` and calls
          ``.result(timeout)`` to block until the watchers exit.  If the
          timeout elapses a warning is logged but the caller is not raised.
        * **Running loop, same thread** (event-loop-thread cleanup path) —
          blocking via ``.result()`` would deadlock, so this path falls
          back to fire-and-forget scheduling while logging a warning.
          Callers that need guaranteed completion should use
          ``await manager.stop()`` directly instead.

        Args:
            shutdown_timeout: Maximum seconds to wait for the watcher loop
                to exit when called from a different thread.  Defaults to
                ``10.0 s``.
        """
        manager = self._watch_manager
        if manager is None or not manager.is_started():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None:
            # No running event loop — drive the coroutine synchronously.
            try:
                asyncio.run(manager.stop())
            except Exception as exc:
                self._logger.debug(
                    "Watch manager stop raised during cleanup: %s", exc, exc_info=True
                )
            return

        # There is a running event loop.  Determine whether this call is
        # arriving from the event-loop thread itself or from a foreign
        # thread (e.g. a signal handler or a cleanup thread).
        loop_thread_id: int | None = getattr(loop, "_thread_id", None)
        on_loop_thread = (
            loop_thread_id is not None and threading.current_thread().ident == loop_thread_id
        )

        if on_loop_thread:
            # Blocking here would deadlock — the event loop cannot make
            # progress while the current frame is suspended.  Schedule
            # fire-and-forget and warn so operators know the watcher may
            # not finish before the process exits.
            self._logger.warning(
                "Kubernetes watcher stop scheduled without awaiting completion "
                "(cleanup called from the event-loop thread; "
                "watchers may outlive this cleanup call)."
            )
            loop.create_task(manager.stop())
            return

        # Foreign thread with a running loop — block with timeout.
        future = asyncio.run_coroutine_threadsafe(manager.stop(), loop)
        try:
            future.result(timeout=shutdown_timeout)
        except TimeoutError:
            self._logger.warning(
                "Kubernetes watcher fleet did not stop within %.1fs; "
                "proceeding with shutdown anyway.",
                shutdown_timeout,
            )
        except Exception as exc:
            self._logger.debug("Watch manager stop raised during cleanup: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Reconciliation / orphan-GC lifecycle
    # ------------------------------------------------------------------

    @property
    def last_reconciliation_report(self) -> Optional[ReconciliationReport]:
        """Surface the most recent :class:`ReconciliationReport` for diagnostics."""
        return self._last_reconciliation_report

    def _shared_cache(self) -> PodStateCache:
        """Return the cache used by both reconciler and watcher.

        Constructed via :meth:`_ensure_watch_manager` so reconciler and
        watcher always share the same instance — populating one
        warms the other.
        """
        return self._ensure_watch_manager().cache

    def _run_startup_reconciler(self) -> None:
        """Run the startup reconciler before the watch task spawns.

        The reconciler is constructed lazily here so tests that pass
        ``startup_reconciler=`` directly into the strategy can skip the
        default construction path entirely.
        """
        reconciler = self._startup_reconciler
        if reconciler is None:
            reconciler = StartupReconciler(
                kubernetes_client=self.kubernetes_client,
                config=self._k8s_config,
                cache=self._shared_cache(),
                logger=self._logger,
                known_request_ids=self._known_request_ids_fn,
            )
            self._startup_reconciler = reconciler
        try:
            self._last_reconciliation_report = reconciler.run()
        except Exception as exc:  # noqa: BLE001 — defensive
            self._logger.warning(
                "Kubernetes startup reconciler raised: %s (provider continues)",
                exc,
                exc_info=True,
            )

    def _maybe_start_orphan_gc(self) -> None:
        """Spawn the orphan GC task when enabled and an event loop is available."""
        if not self._k8s_config.orphan_gc_enabled:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._logger.debug(
                "Skipping orphan GC startup: no running event loop "
                "(GC will not run in this process)."
            )
            return
        if self._orphan_gc is None:
            self._orphan_gc = OrphanGarbageCollector(
                kubernetes_client=self.kubernetes_client,
                config=self._k8s_config,
                logger=self._logger,
                known_request_ids=self._known_request_ids_fn,
            )
        try:
            self._orphan_gc.start()
            self._logger.info(
                "Kubernetes orphan GC started (interval=%ss, auto_cleanup=%s)",
                self._k8s_config.orphan_gc_interval_seconds,
                self._k8s_config.auto_cleanup_orphans,
            )
        except Exception as exc:
            self._logger.warning("Failed to start Kubernetes orphan GC: %s", exc, exc_info=True)

    def _stop_orphan_gc_sync(self) -> None:
        """Stop the orphan GC from a sync-or-async cleanup context."""
        gc = self._orphan_gc
        if gc is None or not gc.is_running():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            try:
                asyncio.run(gc.stop())
            except Exception as exc:
                self._logger.debug("Orphan GC stop raised during cleanup: %s", exc, exc_info=True)
            return
        loop.create_task(gc.stop())

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
        return self._node_state_cache

    def _maybe_start_node_watcher(self) -> None:
        """Start the node watcher when enabled by config.

        Unlike the asyncio pod watcher, the node watcher runs on a
        plain background daemon thread so it does not require a running
        event loop.  This means it can start from both synchronous
        (CLI bootstrap) and async (daemon) contexts.
        """
        if not self._k8s_config.node_watch_enabled:
            return
        if self._node_watcher is None:
            self._node_watcher = K8sNodeWatcher(
                kubernetes_client=self.kubernetes_client,
                cache=self._node_state_cache,
                logger=self._logger,
            )
        try:
            self._node_watcher.start()
            self._logger.info("Kubernetes node watcher started (node_watch_enabled=True)")
        except Exception as exc:
            self._logger.warning("Failed to start Kubernetes node watcher: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Operation dispatch
    # ------------------------------------------------------------------

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """Execute a provider operation.

        Only ``HEALTH_CHECK`` is serviced through this entry point.
        Resource-lifecycle operations are dispatched via the typed
        ``acquire`` / ``return_machines`` / ``get_status`` entry points
        and return an ``UNSUPPORTED_OPERATION`` error here.
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
                    f"Operation {operation.operation_type} is not supported on the "
                    "kubernetes provider's untyped dispatch path; use the typed "
                    "acquire/return_machines/get_status entry points instead.",
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
    # Capabilities & health
    # ------------------------------------------------------------------

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="k8s",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.HEALTH_CHECK,
            ],
            supported_apis=list(self._SUPPORTED_APIS),
            features={
                # Selective termination support varies by provider_api:
                #   Pod         — delete individual pods by name (fully selective)
                #   Deployment  — pod-deletion-cost annotation + replicas patch
                #   StatefulSet — pod-deletion-cost annotation (highest-ordinal first)
                #   Job         — deletes the whole Job regardless of machine_ids
                #                 (NOT selective)
                # The dict below is the authoritative per-API declaration.
                # Callers that need a single boolean should treat the
                # provider as selective only when their target api is in
                # the True set.
                "selective_termination": False,
                "selective_termination_by_api": {
                    "Pod": True,
                    "Deployment": True,
                    "StatefulSet": True,
                    "Job": False,
                },
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

        from orb.providers.k8s.health import register_k8s_health_checks

        register_k8s_health_checks(health_check, client)

    # ------------------------------------------------------------------
    # Native-spec resolution — kept on the strategy because it owns the
    # DI container / config-port plumbing
    # ------------------------------------------------------------------

    def _resolve_native_spec_service(self) -> Optional[Any]:
        """Resolve :class:`K8sNativeSpecService` once on first handler build.

        Caches the result (including the negative resolution) so the
        lookup cost — and any warning — fires at most once per strategy
        instance.  Returns ``None`` when the provider config opts out
        (``native_spec_enabled=False``) or when the generic service is
        not available in the DI container.
        """
        if self._native_spec_service_resolved:
            return self._k8s_native_spec_service
        self._native_spec_service_resolved = True

        if not self._k8s_config.native_spec_enabled:
            return None

        if self._config_port is None:
            self._logger.debug(
                "Kubernetes native-spec service unavailable: no ConfigurationPort "
                "wired into the strategy (typed builder path will be used)."
            )
            return None

        try:
            from orb.application.services.native_spec_service import NativeSpecService
            from orb.infrastructure.di.container import get_container
            from orb.providers.k8s.infrastructure.services.k8s_native_spec_service import (
                K8sNativeSpecService,
            )

            container = get_container()
            self._k8s_native_spec_service = K8sNativeSpecService(
                native_spec_service=container.get(NativeSpecService),
                config_port=self._config_port,
                k8s_config=self._k8s_config,
            )
            return self._k8s_native_spec_service
        except Exception as exc:
            self._logger.warning(
                "K8sNativeSpecService unavailable, native spec enrichment disabled: %s",
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Handler dispatch — delegated to K8sHandlerRegistry
    # ------------------------------------------------------------------

    def _resolve_provider_api(self, request: "Request") -> str:
        """Pick the provider-API key for ``request``."""
        return self._handler_registry.resolve_provider_api(request)

    def _get_handler(self, provider_api: str) -> K8sHandlerBase:
        """Return (and lazily construct) the handler for ``provider_api``."""
        return self._handler_registry.get_handler(provider_api)

    # ------------------------------------------------------------------
    # Typed provisioning interface
    # ------------------------------------------------------------------

    async def acquire(self, request: "Request") -> OperationOutcome:
        """Submit an acquisition request to Kubernetes via the per-API handler."""
        return await self._handler_registry.acquire(request)

    async def return_machines(self, machine_ids: list[str], request: "Request") -> OperationOutcome:
        """Delete the named pods via the per-API handler."""
        return await self._handler_registry.return_machines(machine_ids, request)

    async def get_status(self, resource_ids: list[str], request: "Request") -> OperationOutcome:
        """Poll the per-API handler's ``check_hosts_status`` for a verdict."""
        return await self._handler_registry.get_status(resource_ids, request)

    def _build_template_for_request(self, request: "Request") -> "Template":
        """Resolve the :class:`Template` carried by ``request``."""
        return self._handler_registry.build_template_for_request(request)

    # ------------------------------------------------------------------
    # Infrastructure discovery — ProviderDiscoveryPort implementation
    # ------------------------------------------------------------------

    def _get_discovery_service(self) -> K8sInfrastructureDiscoveryService:
        """Return the infrastructure discovery service, constructing it lazily."""
        if self._discovery_service is None:
            self._discovery_service = K8sInfrastructureDiscoveryService(
                config=self._k8s_config,
                logger=self._logger,
            )
        return self._discovery_service

    def discover_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover Kubernetes infrastructure for the configured cluster.

        Delegates to :class:`K8sInfrastructureDiscoveryService`.  Returns
        a valid (empty-stub) discovery dict until Phase B is implemented.
        """
        return self._get_discovery_service().discover_infrastructure(provider_config)

    def discover_infrastructure_interactive(
        self, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Interactively discover Kubernetes infrastructure via operator prompts.

        Delegates to :class:`K8sInfrastructureDiscoveryService`.  Returns
        the same scaffold as :meth:`discover_infrastructure` until Phase C
        is implemented.
        """
        return self._get_discovery_service().discover_infrastructure_interactive(provider_config)

    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate that the configured Kubernetes cluster is reachable.

        Delegates to :class:`K8sInfrastructureDiscoveryService`.  Returns
        ``{provider, valid: True, issues: []}`` until Phase D is implemented.
        """
        return self._get_discovery_service().validate_infrastructure(provider_config)

    def __str__(self) -> str:  # pragma: no cover — trivial
        return (
            "K8sProviderStrategy("
            f"namespace={self._k8s_config.namespace}, "
            f"initialized={self._initialized})"
        )

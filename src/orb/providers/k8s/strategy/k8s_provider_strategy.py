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
from orb.providers.k8s.services.infrastructure_discovery_service import (
    K8sInfrastructureDiscoveryService,
)
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry
from orb.providers.k8s.value_objects import KubernetesProviderApi
from orb.providers.k8s.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.k8s.watch.node_state_cache import K8sNodeStateCache
from orb.providers.k8s.watch.node_watcher import K8sNodeWatcher
from orb.providers.k8s.watch.pod_state_cache import PodStateCache

_logger = get_logger(__name__)

# Sentinel value used when the user selects the in-cluster ServiceAccount
# credential source.  Accepted both as ``"in_cluster"`` (canonical) and
# ``"in-cluster"`` (hyphenated alias) via ``_normalise_sentinel``.
_IN_CLUSTER_SENTINEL = "in_cluster"


def _normalise_sentinel(value: str) -> str:
    """Normalise ``"in-cluster"`` → ``"in_cluster"`` for sentinel comparison.

    Both spellings are accepted so that YAML configs, CLI flags and the
    ``get_available_credential_sources`` output are mutually compatible.
    """
    return value.replace("-", "_").lower()


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
        # construction.  ``None`` after resolution means the service is
        # unavailable (jinja2 missing, injected service not provided, etc.)
        # — handlers fall back to the typed builder path.  The injected
        # ``native_spec_service`` is the raw ``NativeSpecService`` from the
        # application layer; :meth:`_resolve_native_spec_service` wraps it
        # in ``K8sNativeSpecService`` on first call.  There is no DI
        # container fallback — callers that need native-spec support must
        # supply the service via this constructor parameter.
        self._injected_native_spec_service: Optional[Any] = native_spec_service
        self._native_spec_service_resolved: bool = False
        self._k8s_native_spec_service: Optional[Any] = None
        # Infrastructure discovery service — constructed lazily by
        # :meth:`_get_discovery_service` on first use.
        self._discovery_service: Optional[K8sInfrastructureDiscoveryService] = None
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
        # Prometheus metrics — constructed lazily on first use so tests
        # that never touch the metrics path do not pollute the global
        # ``prometheus_client.REGISTRY``.  Disabled entirely when
        # ``config.metrics_enabled=False``.
        self._metrics: Optional[Any] = None
        # Handler registry — does the per-API handler factory wiring and
        # the typed acquire/return/status dispatch.  Wired with closures
        # over the strategy's lazy client, watcher, native-spec accessors
        # and the per-instance plugin factory dict so the registry never
        # re-implements those lifecycles.
        self._handler_registry = K8sHandlerRegistry(
            config=self._k8s_config,
            logger=self._logger,
            client_provider=lambda: self.kubernetes_client,
            watch_manager_provider=lambda: self._watch_manager,
            plugin_factories=lambda: self._handler_factories,
            native_spec_service_provider=self._resolve_native_spec_service,
            handler_overrides=handler_overrides,
            node_state_cache_provider=lambda: self._node_state_cache,
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
            if self._node_watcher is not None:
                self._node_watcher.stop()
                self._node_watcher = None
        except Exception as exc:
            self._logger.warning(
                "Failed to stop Kubernetes node watcher during cleanup: %s", exc, exc_info=True
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
                metrics=self._get_metrics(),
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
            # MultiNamespaceWatcher.start() is idempotent: it guards on
            # ``self._started`` at entry and returns immediately on a
            # second call.  A SIGTERM that interrupts between the
            # subsystem calls in start_daemon_services() therefore does
            # not leave duplicate watchers running — a retry simply
            # no-ops on the already-started watcher.
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

    def _get_metrics(self) -> Optional[Any]:
        """Return the shared :class:`K8sMetrics` instance, constructing on demand.

        Returns ``None`` when ``config.metrics_enabled=False`` so
        handlers and the watcher stay silent.  Constructed once per
        strategy instance; a second invocation returns the same
        object so all recorders share the same OTel meter.
        """
        if not self._k8s_config.metrics_enabled:
            return None
        if self._metrics is None:
            from orb.providers.k8s.infrastructure.services.metrics import K8sMetrics

            self._metrics = K8sMetrics()
        return self._metrics

    def _shared_cache(self) -> PodStateCache:
        """Return the cache used by both reconciler and watcher.

        Constructed via :meth:`_ensure_watch_manager` so reconciler and
        watcher always share the same instance — populating one
        warms the other.
        """
        return self._ensure_watch_manager().cache

    async def _run_startup_reconciler(self) -> None:
        """Run the startup reconciler before the watch task spawns.

        The reconciler is constructed lazily here so tests that pass
        ``startup_reconciler=`` directly into the strategy can skip the
        default construction path entirely.  Only called from
        :meth:`start_daemon_services` — the CLI path never reaches this
        method.
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
            report = await reconciler.run_async()
            self._last_reconciliation_report = report
            # Only signal first-sync-complete when the reconciler
            # actually succeeded.  ``run_async`` captures its own
            # exceptions internally and sets ``report.completed = False``
            # + ``report.error`` when the LIST failed — gating on the
            # flag prevents ``is_healthy()`` from returning True with a
            # cold, empty cache after a silent reconciler failure.
            if report.completed and self._watch_manager is not None:
                self._watch_manager.mark_first_sync_complete()
            elif not report.completed:
                self._logger.warning(
                    "Kubernetes startup reconciler did not complete: %s "
                    "(provider continues; is_healthy will report False until "
                    "the next successful sync)",
                    report.error,
                )
        except Exception as exc:
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

    def _stop_orphan_gc_sync(self, *, stop_timeout: float = 5.0) -> None:
        """Stop the orphan GC from a sync-or-async cleanup context.

        Three paths depending on the calling context:

        * **No running loop** — drives ``gc.stop()`` synchronously via
          :func:`asyncio.run`.
        * **Running loop, different thread** — schedules the coroutine via
          :func:`asyncio.run_coroutine_threadsafe` and blocks with a timeout
          so the coroutine completes before the client is closed.  If the
          timeout elapses a warning is logged.
        * **Running loop, same thread** — blocking here would deadlock, so
          this path also uses :func:`asyncio.run_coroutine_threadsafe` but
          from a dedicated daemon thread, ensuring the coroutine finishes
          before this method returns.

        Args:
            stop_timeout: Maximum seconds to wait for the GC coroutine to
                finish.  Defaults to ``5.0 s``.
        """
        gc = self._orphan_gc
        if gc is None or not gc.is_running():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            # No running event loop — drive the coroutine synchronously.
            try:
                asyncio.run(gc.stop())
            except Exception as exc:
                self._logger.debug("Orphan GC stop raised during cleanup: %s", exc, exc_info=True)
            return

        # There is a running event loop.  Use run_coroutine_threadsafe from
        # the current thread (or a helper thread) to schedule and block on
        # gc.stop().  This is safe regardless of whether we are on the loop
        # thread itself or a foreign thread because the future is resolved
        # asynchronously by the loop.
        future = asyncio.run_coroutine_threadsafe(gc.stop(), loop)
        try:
            future.result(timeout=stop_timeout)
        except TimeoutError:
            self._logger.warning(
                "Kubernetes orphan GC did not stop within %.1fs during cleanup; "
                "proceeding anyway — the GC coroutine may still be running.",
                stop_timeout,
            )
        except Exception as exc:
            self._logger.debug("Orphan GC stop raised during cleanup: %s", exc, exc_info=True)

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
            elif operation.operation_type == ProviderOperationType.TERMINATE_INSTANCES:
                result = await self._handle_terminate_instances(operation)
            elif operation.operation_type == ProviderOperationType.GET_INSTANCE_STATUS:
                result = await self._handle_get_instance_status(operation)
            elif operation.operation_type == ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES:
                result = await self._handle_describe_resource_instances(operation)
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
        outcome = await self.return_machines(machine_ids, request)
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
                ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
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
        """Probe the Kubernetes API server via ``CoreV1Api.get_api_resources``.

        On success the status message and ``error_details`` dict are enriched
        with the cluster endpoint, server version, and current namespace so
        operators running against multiple clusters can identify which cluster
        the probe hit.  Each enrichment field is fetched defensively — a
        failure to retrieve the server version or endpoint never causes the
        overall health check to fail.
        """
        start = time.time()
        try:
            client = self.kubernetes_client
            resources = client.core_v1.get_api_resources()
            response_time_ms = (time.time() - start) * 1000.0
            resource_count = len(getattr(resources, "resources", []) or [])

            # --- enrichment: cluster endpoint ---
            # Gathers: api_client.configuration.host (apiserver URL).
            cluster_endpoint: Optional[str] = None
            try:
                cluster_endpoint = getattr(
                    client.api_client.configuration,  # type: ignore[union-attr]
                    "host",
                    None,
                )
            except Exception as exc:
                self._logger.debug("Could not read cluster endpoint from api_client: %s", exc)

            # --- enrichment: server version ---
            # Gathers: VersionApi.get_code().git_version (Kubernetes server semver).
            server_version: Optional[str] = None
            try:
                from kubernetes.client import VersionApi

                version_info = VersionApi(api_client=client.api_client).get_code()
                server_version = getattr(version_info, "git_version", None)
            except Exception as exc:
                self._logger.debug("Could not read server version from VersionApi: %s", exc)

            # --- enrichment: current namespace ---
            # Gathers: in-cluster SA namespace from /var/run/secrets/…/namespace.
            current_namespace: Optional[str] = self._k8s_config.namespace
            if current_namespace is None and self._k8s_config.in_cluster:
                try:
                    from orb.providers.k8s.configuration.config import (
                        _read_in_cluster_namespace,
                    )

                    current_namespace = _read_in_cluster_namespace()
                except Exception as exc:
                    self._logger.debug("Could not read in-cluster namespace: %s", exc)

            parts: list[str] = [
                f"Kubernetes API server reachable; {resource_count} core/v1 resources"
            ]
            if cluster_endpoint:
                parts.append(f"endpoint={cluster_endpoint}")
            if server_version:
                parts.append(f"version={server_version}")
            if current_namespace:
                parts.append(f"namespace={current_namespace}")

            details: dict[str, Any] = {"response_time_ms": response_time_ms}
            if cluster_endpoint:
                details["cluster_endpoint"] = cluster_endpoint
            if server_version:
                details["server_version"] = server_version
            if current_namespace:
                details["namespace"] = current_namespace

            return ProviderHealthStatus(
                is_healthy=True,
                status_message="; ".join(parts),
                response_time_ms=response_time_ms,
                error_details=details,
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

    @classmethod
    def generate_provider_name(cls, config: dict[str, Any]) -> str:
        """Generate a Kubernetes provider instance name.

        Pattern: ``k8s_{sanitized_context}``.

        The kubeconfig context name is sanitised by replacing any character
        outside ``[a-zA-Z0-9_-]`` with a hyphen — this handles ARN-style
        context names (colons, slashes) gracefully.

        Lookup and sentinel resolution:

        1. ``context`` key — treated as a real kubeconfig context name.
           It is sanitised directly without sentinel interpretation: even
           if the context is literally named ``"in-cluster"`` it is treated
           as a kubeconfig context, not the in-cluster ServiceAccount path.
        2. No context → fall back to ``k8s_in-cluster`` (in-cluster or
           ambient kubeconfig default — the name signals no explicit
           cluster was targeted).

        Disambiguation: a kubeconfig context whose sanitised form is
        ``"in-cluster"`` (i.e. the literal context name ``"in-cluster"``)
        would collide with the sentinel output ``k8s_in-cluster``.  It is
        instead prefixed with ``ctx_`` → ``k8s_ctx_in-cluster``.

        Note: the legacy ``profile`` key from pre-k8s-provider ``orb init``
        writes is no longer consulted.  Callers that previously relied on
        the ``profile`` fallback should pass ``context`` explicitly.
        """
        import re

        raw_context: str | None = config.get("context") or None

        if raw_context:
            # Real kubeconfig context name — sanitise and check for collision.
            sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "-", raw_context)
            # Guard: a literal context named "in-cluster" would collide with
            # the sentinel output; prefix with ctx_ to disambiguate.
            if sanitized == "in-cluster":
                sanitized = f"ctx_{sanitized}"
            return f"k8s_{sanitized}"

        # No context → in-cluster sentinel.
        return "k8s_in-cluster"

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
        if not provider_name.startswith("k8s_"):
            return {}
        suffix = provider_name[len("k8s_") :]
        return {"context_or_namespace": suffix}

    def get_provider_name_pattern(self) -> str:
        return "k8s_{sanitized_context}"

    def get_supported_apis(self) -> list[str]:
        return list(self._SUPPORTED_APIS)

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
        """Return k8s-specific UI column descriptors for machines, requests, and templates."""
        from orb.application.dto.system import UIColumnDescriptor

        return [
            # ------------------------------------------------------------------
            # machines — pod/workload-level columns
            # ------------------------------------------------------------------
            UIColumnDescriptor(
                key="k8s_namespace",
                path="provider_data.namespace",
                label="Namespace",
                kind="badge",
                resource_type="machines",
                provider="k8s",
                sortable=True,
                default_visible=True,
            ),
            UIColumnDescriptor(
                key="k8s_node_name",
                path="provider_data.node_name",
                label="Node",
                kind="text",
                resource_type="machines",
                provider="k8s",
                sortable=True,
                default_visible=True,
            ),
            UIColumnDescriptor(
                key="k8s_phase",
                path="provider_data.phase",
                label="Phase",
                kind="badge",
                resource_type="machines",
                provider="k8s",
                badge_color_map={
                    "Running": "green",
                    "Pending": "orange",
                    "Succeeded": "teal",
                    "Failed": "red",
                    "Unknown": "gray",
                },
                sortable=True,
                default_visible=True,
            ),
            UIColumnDescriptor(
                key="k8s_restart_count",
                path="provider_data.restart_count",
                label="Restarts",
                kind="count",
                resource_type="machines",
                provider="k8s",
                sortable=True,
                default_visible=False,
            ),
            UIColumnDescriptor(
                key="k8s_capacity_type",
                path="provider_data.node_capacity_type",
                label="Capacity Type",
                kind="badge",
                resource_type="machines",
                provider="k8s",
                badge_color_map={"spot": "orange", "on-demand": "blue", "on_demand": "blue"},
                sortable=True,
                default_visible=False,
            ),
            UIColumnDescriptor(
                key="k8s_workload_kind",
                path="provider_api",
                label="Workload Kind",
                kind="badge",
                resource_type="machines",
                provider="k8s",
                badge_color_map={
                    "Pod": "blue",
                    "Deployment": "purple",
                    "StatefulSet": "teal",
                    "Job": "orange",
                },
                sortable=True,
                default_visible=False,
            ),
            # ------------------------------------------------------------------
            # requests — provider-level request columns
            # ------------------------------------------------------------------
            UIColumnDescriptor(
                key="k8s_request_namespace",
                path="provider_data.namespace",
                label="Namespace",
                kind="badge",
                resource_type="requests",
                provider="k8s",
                sortable=True,
                default_visible=True,
            ),
            UIColumnDescriptor(
                key="k8s_request_provider_api",
                path="provider_data.provider_api",
                label="Workload Kind",
                kind="badge",
                resource_type="requests",
                provider="k8s",
                badge_color_map={
                    "Pod": "blue",
                    "Deployment": "purple",
                    "StatefulSet": "teal",
                    "Job": "orange",
                },
                default_visible=True,
            ),
            # ------------------------------------------------------------------
            # templates — k8s template surface
            # ------------------------------------------------------------------
            UIColumnDescriptor(
                key="k8s_template_provider_api",
                path="provider_api",
                label="Workload Kind",
                kind="badge",
                resource_type="templates",
                provider="k8s",
                badge_color_map={
                    "Pod": "blue",
                    "Deployment": "purple",
                    "StatefulSet": "teal",
                    "Job": "orange",
                },
                default_visible=True,
                sortable=True,
            ),
            UIColumnDescriptor(
                key="k8s_template_namespace",
                path="namespace",
                label="Namespace",
                kind="text",
                resource_type="templates",
                provider="k8s",
                default_visible=True,
                sortable=True,
            ),
        ]

    # ------------------------------------------------------------------
    # Region / CLI helpers
    # ------------------------------------------------------------------

    @classmethod
    def get_available_regions(cls) -> list[tuple[str, str]]:
        """Kubernetes has contexts, not regions — return an empty list."""
        return []

    @classmethod
    def get_default_region(cls) -> str:
        """Kubernetes has no region concept; return an empty string."""
        return ""

    @classmethod
    def get_cli_extra_config_keys(cls) -> set[str]:
        """Return k8s keys that belong in provider config, not template_defaults.

        ``init_command_handler`` calls this after ``discover_infrastructure_interactive``
        to route each discovered field to the correct section.  Keys returned
        here land in ``provider_instance.config``; all other keys land in
        ``provider_instance.template_defaults``.

        Routing table (connection-level → config):
        - ``context``    → ``K8sProviderConfig.context``   (out-of-cluster only)
        - ``in_cluster`` → ``K8sProviderConfig.in_cluster``
        - ``namespace``  → ``K8sProviderConfig.namespace``

        Template-default-level keys NOT in this set (→ template_defaults):
        - ``service_account``    → ``K8sTemplate.service_account``
        - ``image_pull_secret``  → ``K8sTemplate.image_pull_secret``
        """
        return {"context", "in_cluster", "namespace"}

    @classmethod
    def get_cli_infrastructure_defaults(cls, args: Any) -> dict[str, Any]:
        """Extract k8s infrastructure defaults from parsed CLI args.

        Kubernetes infrastructure is cluster-scoped and not decomposed into
        subnet/security-group level constructs like AWS.  Returns an empty
        dict — any relevant cluster-level config comes from
        :meth:`get_cli_provider_config` instead.
        """
        return {}

    @classmethod
    def get_cli_provider_config(cls, args: Any) -> dict[str, Any]:
        """Extract Kubernetes provider config keys from parsed CLI args.

        Reads the three kubernetes-scoped flags registered via
        :class:`~orb.providers.k8s.cli.k8s_cli_spec.K8sCLISpec`:

        * ``--kube-context``  → ``context`` key
        * ``--kubeconfig``    → ``kubeconfig_path`` key
        * ``--namespace``     → ``namespace`` key

        Fields that were not supplied by the operator are omitted from the
        returned dict so that the ``_get_default_config`` overlay can fill
        the remaining gaps without overwriting explicit values with ``None``.

        Args:
            args: Parsed ``argparse.Namespace`` from the ``orb init`` or
                ``orb provider add`` invocation.

        Returns:
            Dict containing only the keys whose values the operator supplied.
        """
        result: dict[str, Any] = {}
        context = getattr(args, "kubernetes_context", None)
        if context is not None:
            result["context"] = context
        kubeconfig = getattr(args, "kubernetes_kubeconfig", None)
        if kubeconfig is not None:
            result["kubeconfig_path"] = kubeconfig
        namespace = getattr(args, "kubernetes_namespace", None)
        if namespace is not None:
            result["namespace"] = namespace
        return result

    @classmethod
    def get_operational_param_choices(cls, param: str) -> list[tuple[str, str]]:
        """Return picker choices for an operational parameter, if any.

        Mirrors :meth:`AWSProviderStrategy.get_operational_param_choices`.
        Kubernetes has no region picker; ``namespace`` is a free-text input so
        an empty list is returned for all parameters, causing the ``orb init``
        prompt to fall back to free-text input.
        """
        return []

    @classmethod
    def get_operational_param_default(cls, param: str) -> str:
        """Return the default value for an operational parameter.

        Mirrors :meth:`AWSProviderStrategy.get_operational_param_default`.
        Kubernetes uses ``"default"`` as the conventional default namespace;
        all other parameters have no meaningful pre-filled default.
        """
        if param == "namespace":
            return "default"
        return ""

    # ------------------------------------------------------------------
    # Credential surface (called by `orb init` and credential probes)
    # ------------------------------------------------------------------

    @classmethod
    def get_available_credential_sources(cls) -> list[dict]:
        """Return Kubernetes credential sources visible to ORB.

        Returns ``in_cluster`` (when a ServiceAccount token is mounted) and
        every available kubeconfig context as a separate selectable source.

        Matches the AWS shape consumed by ``init_command_handler``: each
        entry has ``name`` (technical identifier — kubeconfig context name
        or ``"in_cluster"``) and ``description`` (human-readable label).
        ``test_credentials`` receives the ``name`` value as
        ``credential_source``.
        """
        sources: list[dict] = []

        try:
            from orb.providers.k8s.auth.in_cluster import is_in_cluster

            if is_in_cluster():
                sources.append(
                    {
                        "name": _IN_CLUSTER_SENTINEL,
                        "description": "in-cluster ServiceAccount",
                        "config_delta": {"in_cluster": True},
                    }
                )
        except Exception as exc:
            _logger.debug("in_cluster detection failed: %s", exc)

        try:
            import kubernetes.config as _k8s_config

            contexts, current = _k8s_config.list_kube_config_contexts()
            current_name = current.get("name") if current else None
            for ctx in contexts or []:
                name = ctx.get("name", "")
                if not name:
                    continue
                marker = " (current)" if name == current_name else ""
                cluster = ctx.get("context", {}).get("cluster", "?")
                if name == cluster:
                    label = f"{name}{marker}"
                else:
                    label = f"{name} → {cluster}{marker}"
                sources.append(
                    {
                        "name": name,
                        "description": label,
                        "config_delta": {"context": name},
                    }
                )
        except Exception as exc:
            _logger.debug("kubeconfig context enumeration failed: %s", exc)

        if not sources:
            sources.append(
                {
                    "name": "default",
                    "description": (
                        "Default credentials resolved by the kubernetes-client SDK "
                        "(in-cluster token or KUBECONFIG / ~/.kube/config)"
                    ),
                    "config_delta": {"context": "default"},
                }
            )

        return sources

    @classmethod
    def test_credentials(cls, credential_source: Optional[str] = None, **kwargs: Any) -> dict:
        """Verify the selected credentials can reach the apiserver.

        Calls ``CoreV1Api().get_api_resources()`` with a short timeout.  On
        success, returns the apiserver endpoint and the resolved current
        namespace (in-cluster) or kubeconfig context (out-of-cluster).
        """
        try:
            import kubernetes.client as _k8s_client
            import kubernetes.config as _k8s_config
            from kubernetes.client.exceptions import ApiException
        except ImportError:
            return {
                "success": False,
                "error": (
                    "The kubernetes SDK is not installed.  Install with: pip install 'orb-py[k8s]'"
                ),
            }

        try:
            if (
                credential_source is not None
                and _normalise_sentinel(credential_source) == _IN_CLUSTER_SENTINEL
            ):
                _k8s_config.load_incluster_config()
                context_label = "in-cluster"
            elif credential_source and credential_source not in ("default", ""):
                # The credential_source is a kubeconfig context name.
                _k8s_config.load_kube_config(context=credential_source)
                context_label = credential_source
            else:
                _k8s_config.load_config()
                context_label = "auto-detected"

            # Build the ApiClient ourselves so we keep a typed handle to the
            # configuration object (the auto-built api_client attribute on
            # CoreV1Api is not exposed by the type stubs).
            from kubernetes.client.api_client import ApiClient

            api_client = ApiClient()
            # kubernetes-stubs-elephant-fork omits the `configuration`
            # attribute from ApiClient; it exists at runtime.
            api_client.configuration.timeout = 5  # type: ignore[attr-defined]
            api = _k8s_client.CoreV1Api(api_client=api_client)
            resources = api.get_api_resources()
            endpoint = api_client.configuration.host  # type: ignore[attr-defined]

            return {
                "success": True,
                "context": context_label,
                "endpoint": endpoint,
                "api_groups": len(resources.resources) if resources else 0,
            }
        except ApiException as exc:
            return {
                "success": False,
                "error": (f"Apiserver rejected the probe ({exc.status}): {exc.reason}"),
            }
        except Exception as exc:  # ConfigException, network errors, etc.
            return {"success": False, "error": str(exc)}

    @classmethod
    def get_credential_requirements(cls) -> dict:
        """Document the Kubernetes credential parameters operators may set."""
        return {
            "kubeconfig_path": {
                "required": False,
                "description": "Path to a kubeconfig file (defaults to KUBECONFIG / ~/.kube/config)",
            },
            "context": {
                "required": False,
                "description": "Kubeconfig context name (defaults to current-context)",
            },
            "namespace": {
                "required": False,
                "description": "Target namespace (defaults to in-cluster SA namespace or 'default')",
            },
        }

    @classmethod
    def get_operational_requirements(cls) -> dict:
        """Document operational parameters the init flow may prompt for.

        Mirrors the AWS shape: each entry is ``{required, description}``.
        Kubernetes has no hard-required parameter at init time — namespace
        is optional (in-cluster mode auto-detects from the SA token,
        out-of-cluster defaults to ``"default"``).
        """
        return {
            "namespace": {
                "required": False,
                "description": (
                    "Target namespace for managed pods "
                    "(defaults to in-cluster SA namespace or 'default')"
                ),
            },
        }

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

        from orb.providers.k8s.health import register_k8s_health_checks

        register_k8s_health_checks(health_check, client)

    # ------------------------------------------------------------------
    # Native-spec resolution — kept on the strategy because it owns the
    # DI container / config-port plumbing
    # ------------------------------------------------------------------

    def _resolve_native_spec_service(self) -> Optional[Any]:
        """Resolve :class:`K8sNativeSpecService` once on first handler build.

        Returns ``None`` when the provider config opts out
        (``native_spec_enabled=False``), when no ``ConfigurationPort`` is
        wired, or when no ``native_spec_service`` was passed at construction
        time.  All construction paths that need native-spec support must
        supply the service via the constructor parameter — the
        :func:`orb.providers.k8s.registration.create_k8s_strategy` factory
        resolves it from the DI container at strategy-creation time and
        passes it explicitly.  There is no ``get_container()`` fallback.
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

        if self._injected_native_spec_service is None:
            self._logger.debug(
                "Kubernetes native-spec service unavailable: no NativeSpecService "
                "injected at construction time (typed builder path will be used).  "
                "Ensure create_k8s_strategy is used so the service is resolved from "
                "the DI container before strategy construction."
            )
            return None

        try:
            from orb.providers.k8s.infrastructure.services.k8s_native_spec_service import (
                K8sNativeSpecService,
            )

            self._k8s_native_spec_service = K8sNativeSpecService(
                native_spec_service=self._injected_native_spec_service,
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

    async def return_machines(self, machine_ids: list[str], request: Request) -> OperationOutcome:
        """Delete the named pods via the per-API handler."""
        return await self._handler_registry.return_machines(machine_ids, request)

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


def _build_provider_result_data(
    *,
    resource_ids: list[str],
    metadata: Optional[dict[str, Any]] = None,
    tracking_request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build the ``ProviderResult.data`` dict for k8s outcomes.

    Resource-vs-machine model (Kubernetes):

    * **Pod handler** — every Pod IS its own resource.  Acquire emits
      ``resource_ids == machine_ids == [pod_name, ...]`` (1:1).  The
      handler also surfaces a per-machine ``instances`` list with the
      pod's ``status``, ``image_id`` and so on, populated lazily by the
      status resolver.
    * **Deployment / StatefulSet / Job handler** — the workload controller
      is the *resource* (1 entry: ``[deployment_name]``); the Pods it
      spawns are the *machines* (N entries, populated by the status
      resolver).  Acquire emits ``machine_ids=[]`` because the
      controller has not yet scheduled any pods.

    The bridge therefore propagates whatever the handler put under
    ``metadata['instances']`` verbatim (it carries the authoritative
    ``resource_id`` ↔ ``machine_id`` mapping per pod) and otherwise
    leaves ``instances`` empty so that downstream machine creation is
    driven by a subsequent status read rather than by manufactured rows.
    """
    meta_dict = dict(metadata or {})
    # ``machine_ids`` are the per-pod identifiers (1 per machine row);
    # ``resource_ids`` are the per-controller (or per-pod for the Pod
    # handler) identifiers.  Both are propagated through ``provider_data``
    # so the application layer can reason about them independently.
    machine_ids = list(meta_dict.get("machine_ids") or [])
    instances = list(meta_dict.get("instances") or [])

    data: dict[str, Any] = {
        "resource_ids": resource_ids,
        "instances": instances,
        # ``instance_ids`` consumed by deprovisioning / sync paths that
        # want the per-pod identifiers.  Falls back to ``resource_ids``
        # for the Pod handler (where resource = machine) when the
        # handler did not put machine_ids in metadata.
        "instance_ids": machine_ids or resource_ids,
        "provider_data": meta_dict,
    }
    if tracking_request_id is not None:
        data["tracking_request_id"] = tracking_request_id
    return data


def _all_instances_terminal(instances: list[dict[str, Any]]) -> bool:
    """Return True when every instance dict has reached a non-pending state.

    Used by the bridge to detect synchronous completions: when an Accepted
    outcome carries ``pending_resource_ids`` but ``metadata['instances']``
    already shows every pod as running, succeeded, or terminated, the request
    has effectively completed and ``fulfillment_final`` should be set so the
    provisioning service does not keep it in IN_PROGRESS indefinitely.

    An empty instances list is not considered terminal — the status resolver
    has not yet populated instance data, so we cannot make a determination.
    """
    if not instances:
        return False
    terminal_states = {"running", "succeeded", "terminated"}
    return all(inst.get("status") in terminal_states for inst in instances)


def _outcome_to_provider_result(
    outcome: OperationOutcome, *, fallback_operation: str
) -> ProviderResult:
    """Translate an :class:`OperationOutcome` into a :class:`ProviderResult`.

    Used by ``execute_operation`` to bridge the kubernetes provider's typed
    provisioning interface back to the shared ``ProviderOperation`` envelope
    that the provisioning orchestration service consumes.

    ``fulfillment_final=True`` is set in two cases:
    * ``Completed`` outcome — always terminal.
    * ``Accepted`` outcome where ``pending_resource_ids`` is non-empty AND
      every instance in ``metadata['instances']`` already has a
      running/terminal status.  This covers Pod handlers that schedule pods
      synchronously so the provisioning service does not keep the request
      in IN_PROGRESS waiting for a state transition that already happened.
    """
    from orb.domain.base.operation_outcome import (
        Accepted,
        Completed,
        Failed,
        RequiresFollowUp,
    )

    if isinstance(outcome, Failed):
        return ProviderResult.error_result(outcome.error, "OPERATION_FAILED").model_copy(
            update={
                "metadata": {
                    **(outcome.metadata or {}),
                    "operation": fallback_operation,
                    "provider": "k8s",
                    "recoverable": outcome.recoverable,
                }
            }
        )

    if isinstance(outcome, Accepted):
        meta = dict(outcome.metadata or {})
        pending = list(outcome.pending_resource_ids)
        if pending and _all_instances_terminal(list(meta.get("instances") or [])):
            # All pods are already in a terminal/running state at accept time —
            # promote to fulfillment_final so the provisioning service closes
            # the request without a redundant status poll.
            meta["fulfillment_final"] = True
        return ProviderResult.success_result(
            _build_provider_result_data(
                resource_ids=pending,
                metadata=meta,
                tracking_request_id=outcome.request_id,
            ),
            {"operation": fallback_operation, "provider": "k8s"},
        )

    if isinstance(outcome, Completed):
        # ``fulfillment_final=True`` signals to the provisioning service
        # that the request has reached a terminal state — without it the
        # request would stay IN_PROGRESS forever.
        meta = {**dict(outcome.metadata or {}), "fulfillment_final": True}
        return ProviderResult.success_result(
            _build_provider_result_data(
                resource_ids=list(outcome.resource_ids),
                metadata=meta,
            ),
            {"operation": fallback_operation, "provider": "k8s"},
        )

    if isinstance(outcome, RequiresFollowUp):
        ctx = outcome.context
        # Both follow-up variants carry an ID list; surface it as
        # ``resource_ids`` so the application layer keeps a handle on
        # what's still pending.  ``follow_up_kind`` and the typed context
        # ride along in ``provider_data`` for the background poller.
        pending_ids: list[str] = list(
            getattr(ctx, "pending_resource_ids", None)
            or getattr(ctx, "pending_instance_ids", None)
            or []
        )
        meta = {
            **dict(outcome.metadata or {}),
            "follow_up_kind": ctx.follow_up_kind,
            "provider_handle": getattr(ctx, "provider_handle", None),
            "expected_terminal_state": getattr(ctx, "expected_terminal_state", None),
        }
        return ProviderResult.success_result(
            _build_provider_result_data(
                resource_ids=pending_ids,
                metadata=meta,
            ),
            {"operation": fallback_operation, "provider": "k8s"},
        )

    return ProviderResult.error_result(
        f"Unknown OperationOutcome variant: {type(outcome).__name__}",
        "UNSUPPORTED_OUTCOME",
    )

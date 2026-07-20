"""Watch-manager lifecycle service for the Kubernetes provider.

Extracted from :class:`orb.providers.k8s.strategy.k8s_provider_strategy.
K8sProviderStrategy` so the strategy shell delegates watch fan-out
construction, start, and shutdown to a cohesive owner — mirroring how the
AWS strategy delegates to focused services.

Owns:

* the lazily-constructed :class:`MultiNamespaceWatcher` and its shared
  :class:`PodStateCache`;
* the lazily-constructed :class:`K8sMetrics` recorder;
* the three-path synchronous shutdown (:meth:`stop_sync`) that has to cope
  with being called with no loop, from a foreign thread, or from the
  event-loop thread itself.

The strategy re-exposes ``_watch_manager`` as a delegating property and
``_ensure_watch_manager`` / ``_maybe_start_watch_manager`` /
``_stop_watch_manager_sync`` / ``_shared_cache`` / ``_get_metrics`` as
delegating methods so the public + test-visible surface is unchanged.  The
:class:`K8sMetrics` recorder is owned solely here and reached through
``_get_metrics``; the strategy does not hold its own ``_metrics`` reference.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.k8s.watch.pod_state_cache import PodStateCache


class K8sWatchManagerLifecycle:
    """Own the watch fan-out lifecycle for a single strategy instance."""

    def __init__(
        self,
        *,
        config: K8sProviderConfig,
        logger: LoggingPort,
        client_provider: Callable[[], K8sClient],
        watch_manager: Optional[MultiNamespaceWatcher] = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._client_provider = client_provider
        # Watch fan-out.  Constructed lazily by :meth:`maybe_start` when
        # ``config.watch_enabled`` is True and no override has been
        # provided.  Tests inject a stub via ``watch_manager``.
        self._watch_manager: Optional[MultiNamespaceWatcher] = watch_manager
        # Prometheus metrics — constructed lazily on first use so tests
        # that never touch the metrics path do not pollute the global
        # ``prometheus_client.REGISTRY``.  Disabled entirely when
        # ``config.metrics_enabled=False``.
        self._metrics: Optional[Any] = None

    # -- state accessors -------------------------------------------------

    @property
    def watch_manager(self) -> Optional[MultiNamespaceWatcher]:
        return self._watch_manager

    @watch_manager.setter
    def watch_manager(self, value: Optional[MultiNamespaceWatcher]) -> None:
        self._watch_manager = value

    @property
    def metrics(self) -> Optional[Any]:
        return self._metrics

    @metrics.setter
    def metrics(self, value: Optional[Any]) -> None:
        self._metrics = value

    # -- metrics ---------------------------------------------------------

    def get_metrics(self) -> Optional[Any]:
        """Return the shared :class:`K8sMetrics` instance, constructing on demand.

        Returns ``None`` when ``config.metrics_enabled=False`` so
        handlers and the watcher stay silent.  Constructed once per
        strategy instance; a second invocation returns the same
        object so all recorders share the same OTel meter.
        """
        if not self._config.metrics_enabled:
            return None
        if self._metrics is None:
            from orb.providers.k8s.infrastructure.services.metrics import K8sMetrics

            self._metrics = K8sMetrics()
        return self._metrics

    # -- construction ----------------------------------------------------

    def ensure(self) -> MultiNamespaceWatcher:
        """Lazily construct (but do NOT start) the watch fan-out.

        Exposed so the startup reconciler can share the watcher's
        :class:`PodStateCache`: the reconciler warms the cache before
        the watcher spawns, then the watcher takes over.  The watcher
        is only started later by :meth:`maybe_start` when an event loop
        is available.
        """
        if self._watch_manager is None:
            self._watch_manager = MultiNamespaceWatcher(
                kubernetes_client=self._client_provider(),
                config=self._config,
                logger=self._logger,
                metrics=self.get_metrics(),
            )
        return self._watch_manager

    def shared_cache(self) -> PodStateCache:
        """Return the cache used by both reconciler and watcher.

        Constructed via :meth:`ensure` so reconciler and watcher always
        share the same instance — populating one warms the other.
        """
        return self.ensure().cache

    # -- start -----------------------------------------------------------

    def maybe_start(self) -> None:
        """Start the watch fleet when enabled by config and a loop is available.

        The fleet runs as an asyncio task and therefore needs a running
        event loop.  When ``initialize`` is called from a synchronous
        context (e.g. CLI bootstrap) we skip startup and let the
        cache-less fallback path serve reads.  Daemon / REST callers
        typically run inside an event loop and pick up the watcher.
        """
        if not self._config.watch_enabled:
            return
        manager = self.ensure()
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

    # -- stop ------------------------------------------------------------

    def stop_sync(self, *, shutdown_timeout: float = 10.0) -> None:
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

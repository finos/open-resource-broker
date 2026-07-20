"""Startup-reconciler + orphan-GC lifecycle service for the Kubernetes provider.

Extracted from :class:`orb.providers.k8s.strategy.k8s_provider_strategy.
K8sProviderStrategy`.  Owns the startup reconciler run and the orphan
garbage-collector start/stop, keeping the strategy shell free of the
reconciliation plumbing while mirroring the AWS strategy's
service-delegation shape.

The strategy re-exposes ``_startup_reconciler`` / ``_orphan_gc`` /
``_last_reconciliation_report`` as delegating properties and
``_run_startup_reconciler`` / ``_maybe_start_orphan_gc`` /
``_stop_orphan_gc_sync`` as delegating methods so the public +
test-visible surface is unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Iterable, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.reconciliation.orphan_gc import OrphanGarbageCollector
from orb.providers.k8s.reconciliation.startup_reconciler import (
    ReconciliationReport,
    StartupReconciler,
)
from orb.providers.k8s.strategy.watch_lifecycle import K8sWatchManagerLifecycle


class K8sReconciliationLifecycle:
    """Own startup reconciliation + orphan-GC for a single strategy instance."""

    def __init__(
        self,
        *,
        config: K8sProviderConfig,
        logger: LoggingPort,
        client_provider: Callable[[], K8sClient],
        watch_lifecycle: K8sWatchManagerLifecycle,
        known_request_ids: Callable[[], Iterable[str]],
        startup_reconciler: Optional[StartupReconciler] = None,
        orphan_gc: Optional[OrphanGarbageCollector] = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._client_provider = client_provider
        self._watch_lifecycle = watch_lifecycle
        self._known_request_ids_fn = known_request_ids
        self._startup_reconciler: Optional[StartupReconciler] = startup_reconciler
        self._orphan_gc: Optional[OrphanGarbageCollector] = orphan_gc
        self._last_reconciliation_report: Optional[ReconciliationReport] = None

    # -- state accessors -------------------------------------------------

    @property
    def startup_reconciler(self) -> Optional[StartupReconciler]:
        return self._startup_reconciler

    @startup_reconciler.setter
    def startup_reconciler(self, value: Optional[StartupReconciler]) -> None:
        self._startup_reconciler = value

    @property
    def orphan_gc(self) -> Optional[OrphanGarbageCollector]:
        return self._orphan_gc

    @orphan_gc.setter
    def orphan_gc(self, value: Optional[OrphanGarbageCollector]) -> None:
        self._orphan_gc = value

    @property
    def last_reconciliation_report(self) -> Optional[ReconciliationReport]:
        return self._last_reconciliation_report

    # -- startup reconciler ---------------------------------------------

    async def run_startup_reconciler(self) -> None:
        """Run the startup reconciler before the watch task spawns.

        The reconciler is constructed lazily here so tests that pass
        ``startup_reconciler=`` directly into the strategy can skip the
        default construction path entirely.  Only called from
        ``start_daemon_services`` — the CLI path never reaches this method.
        """
        reconciler = self._startup_reconciler
        if reconciler is None:
            reconciler = StartupReconciler(
                kubernetes_client=self._client_provider(),
                config=self._config,
                cache=self._watch_lifecycle.shared_cache(),
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
            watch_manager = self._watch_lifecycle.watch_manager
            if report.completed and watch_manager is not None:
                watch_manager.mark_first_sync_complete()
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

    # -- orphan GC -------------------------------------------------------

    def maybe_start_orphan_gc(self) -> None:
        """Spawn the orphan GC task when enabled and an event loop is available."""
        if not self._config.orphan_gc_enabled:
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
                kubernetes_client=self._client_provider(),
                config=self._config,
                logger=self._logger,
                known_request_ids=self._known_request_ids_fn,
            )
        try:
            self._orphan_gc.start()
            self._logger.info(
                "Kubernetes orphan GC started (interval=%ss, auto_cleanup=%s)",
                self._config.orphan_gc_interval_seconds,
                self._config.auto_cleanup_orphans,
            )
        except Exception as exc:
            self._logger.warning("Failed to start Kubernetes orphan GC: %s", exc, exc_info=True)

    def stop_orphan_gc_sync(self, *, stop_timeout: float = 5.0) -> None:
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

"""Node-watcher + events-watcher lifecycle service for the Kubernetes provider.

Extracted from :class:`orb.providers.k8s.strategy.k8s_provider_strategy.
K8sProviderStrategy`.  Both watchers run on plain background daemon threads
(not the asyncio loop) so they can start from synchronous CLI bootstrap and
async daemon contexts alike.  Grouping them keeps the shared caches and the
start/stop symmetry in one cohesive owner.

The strategy re-exposes ``_node_watcher`` / ``_events_watcher`` /
``node_state_cache`` / ``node_events_cache`` as delegating attributes and
``_maybe_start_node_watcher`` / ``_maybe_start_events_watcher`` as delegating
methods so the public + test-visible surface is unchanged.
"""

from __future__ import annotations

from typing import Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.watch.events_watcher import K8sEventsWatcher, K8sNodeEventsCache
from orb.providers.k8s.watch.node_state_cache import K8sNodeStateCache
from orb.providers.k8s.watch.node_watcher import K8sNodeWatcher


class K8sNodeWatchLifecycle:
    """Own the node-watcher + events-watcher lifecycle for a single strategy instance."""

    def __init__(
        self,
        *,
        config: K8sProviderConfig,
        logger: LoggingPort,
        client_provider: Callable[[], K8sClient],
        node_state_cache: Optional[K8sNodeStateCache] = None,
        node_watcher: Optional[K8sNodeWatcher] = None,
        node_events_cache: Optional[K8sNodeEventsCache] = None,
        events_watcher: Optional[K8sEventsWatcher] = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._client_provider = client_provider
        # Node watching.  When ``node_watch_enabled=True`` (opt-in via
        # K8sProviderConfig) the strategy starts a K8sNodeWatcher on the
        # background thread and exposes the populated K8sNodeStateCache to
        # handlers so per-instance status dicts carry node metadata.
        self._node_state_cache: K8sNodeStateCache = node_state_cache or K8sNodeStateCache()
        self._node_watcher: Optional[K8sNodeWatcher] = node_watcher
        # Events API watching.  When ``events_watch_enabled=True`` (opt-in via
        # K8sProviderConfig) the strategy starts a K8sEventsWatcher on a
        # background thread and populates K8sNodeEventsCache with Karpenter
        # node-disruption events.
        self._node_events_cache: K8sNodeEventsCache = node_events_cache or K8sNodeEventsCache()
        self._events_watcher: Optional[K8sEventsWatcher] = events_watcher

    # -- state accessors -------------------------------------------------

    @property
    def node_state_cache(self) -> K8sNodeStateCache:
        return self._node_state_cache

    @property
    def node_events_cache(self) -> K8sNodeEventsCache:
        return self._node_events_cache

    @property
    def node_watcher(self) -> Optional[K8sNodeWatcher]:
        return self._node_watcher

    @node_watcher.setter
    def node_watcher(self, value: Optional[K8sNodeWatcher]) -> None:
        self._node_watcher = value

    @property
    def events_watcher(self) -> Optional[K8sEventsWatcher]:
        return self._events_watcher

    @events_watcher.setter
    def events_watcher(self, value: Optional[K8sEventsWatcher]) -> None:
        self._events_watcher = value

    # -- node watcher ----------------------------------------------------

    def maybe_start_node_watcher(self) -> None:
        """Start the node watcher when enabled by config.

        Unlike the asyncio pod watcher, the node watcher runs on a
        plain background daemon thread so it does not require a running
        event loop.  This means it can start from both synchronous
        (CLI bootstrap) and async (daemon) contexts.
        """
        if not self._config.node_watch_enabled:
            return
        if self._node_watcher is None:
            self._node_watcher = K8sNodeWatcher(
                kubernetes_client=self._client_provider(),
                cache=self._node_state_cache,
                logger=self._logger,
            )
        try:
            self._node_watcher.start()
            self._logger.info("Kubernetes node watcher started (node_watch_enabled=True)")
        except Exception as exc:
            self._logger.warning("Failed to start Kubernetes node watcher: %s", exc, exc_info=True)

    # -- events watcher --------------------------------------------------

    def maybe_start_events_watcher(self) -> None:
        """Start the Events API watcher when enabled by config.

        Like the node watcher, the events watcher runs on a plain
        background daemon thread (not in the asyncio event loop) so it
        can start from both synchronous (CLI bootstrap) and async
        (daemon) contexts.

        Requires the operator to have granted the ``events: get/list/watch``
        RBAC verb on the core API group -- see
        ``docs/root/providers/k8s/rbac.yaml``.
        """
        if not self._config.events_watch_enabled:
            return
        if self._events_watcher is None:
            self._events_watcher = K8sEventsWatcher(
                kubernetes_client=self._client_provider(),
                cache=self._node_events_cache,
                logger=self._logger,
            )
        try:
            self._events_watcher.start()
            self._logger.info("Kubernetes events watcher started (events_watch_enabled=True)")
        except Exception as exc:
            self._logger.warning(
                "Failed to start Kubernetes events watcher: %s", exc, exc_info=True
            )

    # -- cleanup ---------------------------------------------------------

    def stop_node_watcher(self) -> None:
        """Stop and clear the node watcher (raises on failure — caller wraps)."""
        if self._node_watcher is not None:
            self._node_watcher.stop()
            self._node_watcher = None

    def stop_events_watcher(self) -> None:
        """Stop and clear the events watcher (raises on failure — caller wraps)."""
        if self._events_watcher is not None:
            self._events_watcher.stop()
            self._events_watcher = None

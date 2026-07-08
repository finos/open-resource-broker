"""Multi-namespace fan-out for the Kubernetes watcher.

Spawns one :class:`~orb.providers.k8s.watch.watcher.K8sWatcher`
per configured namespace and shares the same
:class:`~orb.providers.k8s.watch.pod_state_cache.PodStateCache`
across them so the cache stays the single source of truth regardless
of how many watchers are running.

Three operating modes — driven by
:attr:`K8sProviderConfig.namespaces`:

* ``None``     — single-namespace mode using ``config.namespace``;
  exactly one watcher is started.
* explicit list (e.g. ``["alpha", "beta"]``) — one watcher per
  namespace; an aggregate health check returns alive only when every
  watcher is alive.
* ``["*"]``    — cluster-scoped mode; one watcher with ``namespace=None``
  is started, using ``CoreV1Api.list_pod_for_all_namespaces`` underneath.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.watch.pod_state_cache import PodStateCache
from orb.providers.k8s.watch.watcher import K8sWatcher, WatchFactory


@injectable
class MultiNamespaceWatcher:
    """Coordinates one :class:`K8sWatcher` per configured namespace.

    The instance owns the shared :class:`PodStateCache`; callers (the
    strategy or the Pod handler) read from it through the cache and
    consult :meth:`is_healthy` to decide between the cache path and the
    on-demand list fallback.

    Args:
        kubernetes_client: Provider's API facade.
        config: Validated :class:`K8sProviderConfig`.
        logger: Logging port.
        cache: Optional shared cache.  When ``None`` a fresh
            :class:`PodStateCache` is created.
        watch_factory: Optional factory passed through to every child
            watcher (tests inject a stub).
    """

    def __init__(
        self,
        kubernetes_client: K8sClient,
        config: K8sProviderConfig,
        logger: LoggingPort,
        *,
        cache: Optional[PodStateCache] = None,
        watch_factory: Optional[WatchFactory] = None,
    ) -> None:
        self._client = kubernetes_client
        self._config = config
        self._logger = logger
        self._cache = cache or PodStateCache()
        self._watch_factory = watch_factory

        self._watchers: list[K8sWatcher] = []
        self._started = False

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def cache(self) -> PodStateCache:
        return self._cache

    @property
    def watchers(self) -> tuple[K8sWatcher, ...]:
        """Immutable tuple of active per-namespace watchers."""
        return tuple(self._watchers)

    def is_started(self) -> bool:
        return self._started

    def is_healthy(self) -> bool:
        """Return ``True`` iff every child watcher reports ``is_running``.

        A multi-watcher fleet is only safe to consult the cache for when
        every namespace is being observed — otherwise one dead watcher
        produces stale entries indistinguishable from running pods.
        """
        if not self._started or not self._watchers:
            return False
        return all(w.is_running() for w in self._watchers)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn one watcher per resolved namespace.

        Idempotent: ``start`` while already running is a no-op.  The
        watcher list is determined from
        :meth:`_resolve_watched_namespaces` which is the single source
        of truth for the namespace mode.
        """
        if self._started:
            return

        namespaces = self._resolve_watched_namespaces()
        if not namespaces:
            self._logger.debug("MultiNamespaceWatcher.start: no namespaces resolved; nothing to do")
            self._started = True
            return

        for ns in namespaces:
            self._watchers.append(self._build_watcher(namespace=ns))

        for watcher in self._watchers:
            watcher.start()
        self._started = True
        self._logger.info(
            "Kubernetes watcher fleet started (namespaces=%s)",
            [w.namespace if w.namespace is not None else "*" for w in self._watchers],
        )

    async def stop(self) -> None:
        """Stop every child watcher and clear the watcher list."""
        if not self._started:
            return
        # Stop in parallel so a slow shutdown for one namespace does
        # not block the rest.
        await asyncio.gather(
            *(w.stop() for w in self._watchers),
            return_exceptions=False,
        )
        self._watchers.clear()
        self._started = False
        self._logger.info("Kubernetes watcher fleet stopped")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_watched_namespaces(self) -> list[Optional[str]]:
        """Translate the provider config into the namespace list to watch.

        Returns a list whose entries are either namespace strings (one
        watcher per entry) or ``None`` (cluster-scoped watch).
        """
        explicit = self._config.namespaces
        if explicit is None:
            return [self._config.namespace]
        if explicit == ["*"]:
            return [None]
        return list(explicit)

    def _build_watcher(self, *, namespace: Optional[str]) -> K8sWatcher:
        """Construct a child :class:`K8sWatcher` for ``namespace``."""
        kwargs: dict[str, object] = {
            "kubernetes_client": self._client,
            "cache": self._cache,
            "logger": self._logger,
            "namespace": namespace,
            "label_selector": f"{self._config.label_prefix}/managed=true",
            "request_id_label": f"{self._config.label_prefix}/request-id",
        }
        if self._watch_factory is not None:
            kwargs["watch_factory"] = self._watch_factory
        return K8sWatcher(**kwargs)  # type: ignore[arg-type]


__all__ = ["MultiNamespaceWatcher"]

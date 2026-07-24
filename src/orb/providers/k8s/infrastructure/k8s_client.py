"""Kubernetes API client facade.

Wraps a single :class:`kubernetes.client.ApiClient` plus lazy accessors for
the per-group typed API clients used by handlers / health checks / watchers
in later phases.

Mirrors the role of
:class:`orb.providers.aws.infrastructure.aws_client.AWSClient` in the AWS
provider — a single chokepoint through which all SDK calls flow so that:

* the ``kubernetes`` SDK import surface stays confined to this package
  (enforced by the architecture test);
* unit tests can swap a mock ``ApiClient`` into a strategy without
  touching every handler;
* cleanup is centralised in one place.

Token refresh
-------------
Two credential-expiry exposures are handled:

* **In-cluster** — the projected service-account token on disk rotates
  periodically (finite TTL).  :class:`InClusterAuthAdapter` tracks the load
  time and :meth:`refresh_if_stale` proactively reloads before the TTL
  lapses.
* **kubeconfig / exec plugin** (e.g. EKS ``aws eks get-token``) — the exec
  plugin mints a short-lived (~15 min) Bearer token.  The kubernetes SDK's
  ``refresh_api_key_hook`` only re-execs when the token's *embedded* expiry
  passes; a token that is rejected for another reason (identity/session
  change, clock skew, revocation) keeps being resent, yielding a 401.

:meth:`force_token_refresh` recovers from a 401 for *both* modes by
re-minting the credential and loading it into the *live* ``ApiClient``'s
``Configuration`` so the already-built typed API clients pick up the new
token without rebuilding the connection pool.  For the kubeconfig / exec
case the re-mint happens simply because the pinned kubernetes SDK's
``ExecProvider`` does no token caching — it spawns the plugin on every
``load_kube_config`` call — so re-running the load always yields a fresh
token.  Because a mass-401 can enter this method from many concurrent retry
workers at once, the re-mint is guarded by a lock + short debounce window so
the stampede coalesces into a single re-exec (the token lives on the one
shared live ``Configuration``, so one refresh serves every waiter).

Steady-state exec re-mints (the SDK's lazy ``refresh_api_key_hook`` firing on
an ordinary request when the embedded token expiry lapses) are handled
separately in :mod:`orb.providers.k8s.auth.kubeconfig`, which wraps that hook
so its exec plugin runs non-interactively regardless of whether ORB is
attached to a terminal.  Because the wrap lives on the ``Configuration`` the
``ApiClient`` reads per request, it covers every request path — resource
verbs, watch-stream opens, status polls — without this facade needing a
per-call wrapper.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.k8s.auth.in_cluster import (
    InClusterAuthAdapter,
    is_in_cluster,
    load_in_cluster_config,
)
from orb.providers.k8s.auth.kubeconfig import load_kubeconfig
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.exceptions.k8s_exceptions import K8sAuthError

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import AppsV1Api, BatchV1Api, CoreV1Api
    from kubernetes.client.api_client import ApiClient

# When a mass-401 fans out across many concurrent retry workers, only the
# first re-mint is useful — the fresh token lands on the single shared live
# Configuration and immediately serves every other waiter.  A re-mint newer
# than this many seconds is treated as still-fresh and skipped, coalescing the
# stampede into one ``aws eks get-token`` (or SA-file re-read) invocation.
_TOKEN_REFRESH_DEBOUNCE_SECONDS = 5.0


class K8sClient:
    """Facade over the kubernetes Python SDK clients used by the provider.

    Args:
        config: Validated :class:`K8sProviderConfig` instance.
        logger: ``LoggingPort`` for structured logging (injected via DI).
        api_client: Optional pre-built ``kubernetes.client.ApiClient``.  When
            provided, the facade skips its own config-loading and adopts the
            supplied client verbatim (primarily used by unit tests).
        token_refresh_seconds: TTL window for the in-cluster token refresh.
            Defaults to 55 minutes.  Only used when the provider is in-cluster.
    """

    def __init__(
        self,
        config: K8sProviderConfig,
        logger: LoggingPort,
        api_client: Optional[ApiClient] = None,
        token_refresh_seconds: Optional[int] = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._api_client: Optional[ApiClient] = api_client
        self._core_v1: Optional[CoreV1Api] = None
        self._apps_v1: Optional[AppsV1Api] = None
        self._batch_v1: Optional[BatchV1Api] = None

        # Coalesce concurrent 401-driven refreshes: the lock serialises the
        # re-mint and ``_last_refresh_at`` records when it last completed so
        # waiters that arrive within the debounce window skip a redundant
        # re-exec (the shared live Configuration already carries a fresh token).
        self._refresh_lock = threading.Lock()
        self._last_refresh_at: Optional[float] = None

        # Auth adapter — only populated for in-cluster auth; None for kubeconfig
        # auth (kubeconfig credentials are typically long-lived certificates).
        refresh_kwargs: dict[str, int] = (
            {"token_refresh_seconds": token_refresh_seconds}
            if token_refresh_seconds is not None
            else {}
        )
        self._in_cluster_adapter: Optional[InClusterAuthAdapter] = (
            InClusterAuthAdapter(
                logger=logger,
                proxy_url=config.proxy_url,
                no_proxy=config.no_proxy,
                **refresh_kwargs,
            )
            if api_client is None
            else None
        )

    # ------------------------------------------------------------------
    # Auth / config loading
    # ------------------------------------------------------------------

    def load_config(self) -> None:
        """Bootstrap the global ``kubernetes`` client config from this provider's settings.

        Resolution order:

        1. If ``config.in_cluster`` is ``True``, force in-cluster loading.
        2. If ``config.in_cluster`` is ``False``, force kubeconfig loading.
        3. Otherwise auto-detect via the in-cluster service-account sentinel.

        When in-cluster loading is selected, the load is tracked by
        :attr:`_in_cluster_adapter` so that :meth:`refresh_if_stale` and the
        401-retry path can reload credentials without re-entering this method.
        """
        if self._api_client is not None:
            # Pre-built client supplied; nothing to load.
            return

        try:
            if self._config.in_cluster is True:
                self._logger.debug("Loading in-cluster Kubernetes config (forced).")
                if self._in_cluster_adapter is not None:
                    self._in_cluster_adapter.load()
                else:
                    load_in_cluster_config(
                        logger=self._logger,
                        proxy_url=self._config.proxy_url,
                        no_proxy=self._config.no_proxy,
                    )
            elif self._config.in_cluster is False:
                self._logger.debug("Loading kubeconfig (in_cluster=False, forced).")
                # In-cluster adapter not used for kubeconfig auth.
                self._in_cluster_adapter = None
                load_kubeconfig(
                    config_file=self._config.kubeconfig_path,
                    context=self._config.context,
                    logger=self._logger,
                    proxy_url=self._config.proxy_url,
                    no_proxy=self._config.no_proxy,
                )
            elif is_in_cluster():
                self._logger.debug("In-cluster sentinel present; loading in-cluster config.")
                if self._in_cluster_adapter is not None:
                    self._in_cluster_adapter.load()
                else:
                    load_in_cluster_config(
                        logger=self._logger,
                        proxy_url=self._config.proxy_url,
                        no_proxy=self._config.no_proxy,
                    )
            else:
                self._logger.debug(
                    "No in-cluster sentinel; loading kubeconfig (path=%s, context=%s).",
                    self._config.kubeconfig_path,
                    self._config.context,
                )
                self._in_cluster_adapter = None
                load_kubeconfig(
                    config_file=self._config.kubeconfig_path,
                    context=self._config.context,
                    logger=self._logger,
                    proxy_url=self._config.proxy_url,
                    no_proxy=self._config.no_proxy,
                )
        except K8sAuthError:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            raise K8sAuthError(f"Kubernetes config loading failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Token refresh helpers
    # ------------------------------------------------------------------

    def refresh_if_stale(self) -> bool:
        """Proactively refresh in-cluster credentials when the token has aged past TTL.

        Returns:
            ``True`` if a refresh was performed, ``False`` otherwise.

        No-op (returns ``False``) when the provider uses kubeconfig auth.
        """
        adapter = self._in_cluster_adapter
        if adapter is None:
            return False
        refreshed = adapter.refresh_if_stale()
        if refreshed:
            self._logger.info("K8sClient: in-cluster service-account token refreshed proactively.")
        return refreshed

    def force_token_refresh(self) -> bool:
        """Force a fresh credential to be minted and loaded into the live client.

        This is the 401-recovery path.  It handles *both* auth modes and,
        crucially, loads the refreshed credential into the **live**
        ``ApiClient``'s ``Configuration`` (via ``client_configuration=``) so
        the already-built ``core_v1`` / ``apps_v1`` / ``batch_v1`` clients — all
        of which share that same ``ApiClient`` — pick up the new Bearer token
        without rebuilding the connection pool.

        * **In-cluster** — re-reads the projected service-account token from
          disk.
        * **kubeconfig / exec plugin** — re-runs ``load_kube_config`` into the
          live configuration.  The pinned kubernetes SDK's ``ExecProvider``
          does no token caching, so each load spawns the plugin (e.g.
          ``aws eks get-token``) afresh and mints a new token — no disk-cache
          manipulation is needed or attempted (clearing ``~/.kube/cache/token``
          would only perturb a co-located ``kubectl`` and does nothing for this
          SDK).

        Concurrency: on a mass-401 many retry workers may call this at once.
        A lock plus a short debounce window (:data:`_TOKEN_REFRESH_DEBOUNCE_SECONDS`)
        coalesces the stampede — the first caller re-mints, and callers that
        acquire the lock and find a just-completed refresh skip the redundant
        re-exec because the shared live ``Configuration`` already holds a fresh
        token.

        A pre-supplied (test-injected) ``ApiClient`` has no config to reload,
        so this is a no-op returning ``False`` in that case.

        Returns:
            ``True`` when a refresh was attempted and succeeded, ``False`` when
            there was nothing to refresh (injected client, no live config).

        Raises:
            K8sAuthError: When the refresh itself fails (propagated so the
                caller can decide whether to re-raise the original 401).
        """
        live_config = self._live_client_configuration()
        if live_config is None:
            # Injected ApiClient (unit tests) — no owned config to reload.
            return False

        with self._refresh_lock:
            # Double-checked: a concurrent worker may have just re-minted while
            # we were blocked on the lock.  The token lives on the shared live
            # Configuration, so a refresh within the debounce window already
            # serves us — skip the redundant re-exec and go straight to retry.
            now = time.monotonic()
            last = self._last_refresh_at
            if last is not None and (now - last) < _TOKEN_REFRESH_DEBOUNCE_SECONDS:
                self._logger.debug(
                    "K8sClient: credential refreshed %.2fs ago (< %.1fs window); "
                    "reusing the fresh token instead of re-minting.",
                    now - last,
                    _TOKEN_REFRESH_DEBOUNCE_SECONDS,
                )
                return True

            if self._is_in_cluster_auth():
                load_in_cluster_config(
                    logger=self._logger,
                    proxy_url=self._config.proxy_url,
                    no_proxy=self._config.no_proxy,
                    client_configuration=live_config,
                )
                adapter = self._in_cluster_adapter
                if adapter is not None:
                    adapter._last_loaded_at = time.monotonic()
            else:
                # kubeconfig / exec-plugin auth: re-run load_kube_config so the
                # (cache-less) exec plugin re-mints straight into the live config.
                load_kubeconfig(
                    config_file=self._config.kubeconfig_path,
                    context=self._config.context,
                    logger=self._logger,
                    proxy_url=self._config.proxy_url,
                    no_proxy=self._config.no_proxy,
                    client_configuration=live_config,
                )
            self._last_refresh_at = time.monotonic()
            return True

    def _is_in_cluster_auth(self) -> bool:
        """Return ``True`` when this client authenticates via in-cluster secrets.

        Mirrors the resolution order in :meth:`load_config`: an explicit
        ``config.in_cluster`` flag wins, otherwise the in-cluster
        service-account sentinel is auto-detected.  Used by
        :meth:`force_token_refresh` to pick the correct re-mint path
        independently of whether :meth:`load_config` has already nulled the
        in-cluster adapter.
        """
        if self._config.in_cluster is True:
            return True
        if self._config.in_cluster is False:
            return False
        return is_in_cluster()

    def _live_client_configuration(self) -> Optional[Any]:
        """Return the ``Configuration`` owned by the live ``ApiClient``, or ``None``.

        The ``ApiClient`` holds its own copy of the ``Configuration`` (it does
        not consult the global default per request), so reloading credentials
        into the global default alone would never reach an in-flight client.
        This returns the live client's own configuration object so refreshed
        credentials land where the requests actually read them.
        """
        client = self._api_client
        if client is None:
            return None
        return getattr(client, "configuration", None)

    # ------------------------------------------------------------------
    # API client accessors
    # ------------------------------------------------------------------

    @property
    def api_client(self) -> ApiClient:
        """Return the underlying ``kubernetes.client.ApiClient``, building one on demand."""
        if self._api_client is None:
            self.load_config()
            try:
                from kubernetes.client.api_client import ApiClient as _ApiClient
            except ImportError as exc:  # pragma: no cover — extra not installed
                raise K8sAuthError(
                    "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
                ) from exc
            self._api_client = _ApiClient()
        return self._api_client

    @property
    def core_v1(self) -> CoreV1Api:
        """Lazy ``CoreV1Api`` accessor (pods, services, namespaces, nodes)."""
        if self._core_v1 is None:
            from kubernetes.client import CoreV1Api as _CoreV1Api

            self._core_v1 = _CoreV1Api(self.api_client)
        return self._core_v1

    @property
    def apps_v1(self) -> AppsV1Api:
        """Lazy ``AppsV1Api`` accessor (Deployment, StatefulSet)."""
        if self._apps_v1 is None:
            from kubernetes.client import AppsV1Api as _AppsV1Api

            self._apps_v1 = _AppsV1Api(self.api_client)
        return self._apps_v1

    @property
    def batch_v1(self) -> BatchV1Api:
        """Lazy ``BatchV1Api`` accessor (Job, CronJob)."""
        if self._batch_v1 is None:
            from kubernetes.client import BatchV1Api as _BatchV1Api

            self._batch_v1 = _BatchV1Api(self.api_client)
        return self._batch_v1

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Release the underlying ``ApiClient`` connection pool.

        Calls ``api_client.close()`` to drain the urllib3 connection pool.
        Idempotent — safe to call multiple times.
        """
        client: Optional[Any] = self._api_client
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # pragma: no cover — defensive
                    self._logger.warning(
                        "Failed to close Kubernetes ApiClient: %s", exc, exc_info=True
                    )
        self._api_client = None
        self._core_v1 = None
        self._apps_v1 = None
        self._batch_v1 = None

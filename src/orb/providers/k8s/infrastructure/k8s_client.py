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
When the provider is running in-cluster the service-account token on disk
rotates periodically (Kubernetes projects tokens with a finite TTL).
:class:`InClusterAuthAdapter` is wired into ``load_config`` so that:

1. A proactive :meth:`InClusterAuthAdapter.refresh_if_stale` call is made
   before every batch of API calls exposed via :meth:`call_with_auth_retry`.
2. An ``ApiException(status=401)`` response causes an immediate token
   reload followed by one retry of the failed call.

Both paths keep the ``ApiClient`` alive — only the underlying credential
material is refreshed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar

from orb.domain.base.ports import LoggingPort
from orb.providers.k8s.auth.in_cluster import (
    InClusterAuthAdapter,
    is_in_cluster,
    load_in_cluster_config,
)
from orb.providers.k8s.auth.kubeconfig import load_kubeconfig
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.exceptions.k8s_errors import K8sAuthError

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import AppsV1Api, BatchV1Api, CoreV1Api
    from kubernetes.client.api_client import ApiClient

_T = TypeVar("_T")


def _is_401(exc: BaseException) -> bool:
    """Return ``True`` when *exc* is an ``ApiException`` with status 401."""
    try:
        from kubernetes.client.exceptions import ApiException  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, ApiException) and getattr(exc, "status", None) == 401


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
        api_client: "Optional[ApiClient]" = None,
        token_refresh_seconds: Optional[int] = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._api_client: "Optional[ApiClient]" = api_client
        self._core_v1: "Optional[CoreV1Api]" = None
        self._apps_v1: "Optional[AppsV1Api]" = None
        self._batch_v1: "Optional[BatchV1Api]" = None

        # Auth adapter — only populated for in-cluster auth; None for kubeconfig
        # auth (kubeconfig credentials are typically long-lived certificates).
        refresh_kwargs: dict[str, int] = (
            {"token_refresh_seconds": token_refresh_seconds}
            if token_refresh_seconds is not None
            else {}
        )
        self._in_cluster_adapter: Optional[InClusterAuthAdapter] = (
            InClusterAuthAdapter(**refresh_kwargs) if api_client is None else None
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
                    load_in_cluster_config()
            elif self._config.in_cluster is False:
                self._logger.debug("Loading kubeconfig (in_cluster=False, forced).")
                # In-cluster adapter not used for kubeconfig auth.
                self._in_cluster_adapter = None
                load_kubeconfig(
                    config_file=self._config.kubeconfig_path,
                    context=self._config.context,
                    logger=self._logger,
                )
            elif is_in_cluster():
                self._logger.debug("In-cluster sentinel present; loading in-cluster config.")
                if self._in_cluster_adapter is not None:
                    self._in_cluster_adapter.load()
                else:
                    load_in_cluster_config()
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

    def call_with_auth_retry(self, fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        """Execute *fn* with a proactive stale-token check and one 401-triggered retry.

        Before calling *fn*, :meth:`refresh_if_stale` is consulted so that
        expired tokens are reloaded before the request is sent.  If the call
        raises an ``ApiException(status=401)``, the credentials are refreshed
        and the call is retried exactly once.  A second 401 is re-raised so
        higher-level retry logic or the operator can investigate.

        Args:
            fn: Callable that performs a kubernetes SDK API call.
            *args: Positional arguments forwarded to *fn*.
            **kwargs: Keyword arguments forwarded to *fn*.

        Returns:
            The return value of *fn*.

        Raises:
            ``ApiException``: When the call fails with a non-401 status code,
                or with 401 on the retry.
        """
        self.refresh_if_stale()

        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not _is_401(exc):
                raise
            self._logger.warning(
                "K8sClient: received 401 Unauthorised; refreshing in-cluster token and retrying."
            )
            # Force a token reload regardless of the TTL.
            adapter = self._in_cluster_adapter
            if adapter is not None:
                try:
                    import time  # noqa: PLC0415

                    load_in_cluster_config()
                    # Update the adapter's timestamp so the TTL window resets.
                    adapter._last_loaded_at = time.monotonic()  # noqa: SLF001
                except K8sAuthError as auth_exc:
                    self._logger.error("K8sClient: token refresh on 401 failed: %s", auth_exc)
                    raise exc from None
            # Single retry.
            return fn(*args, **kwargs)

    # ------------------------------------------------------------------
    # API client accessors
    # ------------------------------------------------------------------

    @property
    def api_client(self) -> "ApiClient":
        """Return the underlying ``kubernetes.client.ApiClient``, building one on demand."""
        if self._api_client is None:
            self.load_config()
            try:
                from kubernetes.client.api_client import ApiClient as _ApiClient  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover — extra not installed
                raise K8sAuthError(
                    "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
                ) from exc
            self._api_client = _ApiClient()
        return self._api_client

    @property
    def core_v1(self) -> "CoreV1Api":
        """Lazy ``CoreV1Api`` accessor (pods, services, namespaces, nodes)."""
        if self._core_v1 is None:
            from kubernetes.client import CoreV1Api as _CoreV1Api  # noqa: PLC0415

            self._core_v1 = _CoreV1Api(self.api_client)
        return self._core_v1

    @property
    def apps_v1(self) -> "AppsV1Api":
        """Lazy ``AppsV1Api`` accessor (Deployment, StatefulSet)."""
        if self._apps_v1 is None:
            from kubernetes.client import AppsV1Api as _AppsV1Api  # noqa: PLC0415

            self._apps_v1 = _AppsV1Api(self.api_client)
        return self._apps_v1

    @property
    def batch_v1(self) -> "BatchV1Api":
        """Lazy ``BatchV1Api`` accessor (Job, CronJob)."""
        if self._batch_v1 is None:
            from kubernetes.client import BatchV1Api as _BatchV1Api  # noqa: PLC0415

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

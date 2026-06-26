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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.kubernetes.auth.in_cluster import (
    is_in_cluster,
    load_in_cluster_config,
)
from orb.providers.kubernetes.auth.kubeconfig import load_kubeconfig
from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.exceptions.k8s_errors import KubernetesAuthError

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import ApiClient, AppsV1Api, BatchV1Api, CoreV1Api


class KubernetesClient:
    """Facade over the kubernetes Python SDK clients used by the provider.

    Args:
        config: Validated :class:`KubernetesProviderConfig` instance.
        logger: ``LoggingPort`` for structured logging (injected via DI).
        api_client: Optional pre-built ``kubernetes.client.ApiClient``.  When
            provided, the facade skips its own config-loading and adopts the
            supplied client verbatim (primarily used by unit tests).
    """

    def __init__(
        self,
        config: KubernetesProviderConfig,
        logger: LoggingPort,
        api_client: "Optional[ApiClient]" = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._api_client: "Optional[ApiClient]" = api_client
        self._core_v1: "Optional[CoreV1Api]" = None
        self._apps_v1: "Optional[AppsV1Api]" = None
        self._batch_v1: "Optional[BatchV1Api]" = None

    # ------------------------------------------------------------------
    # Auth / config loading
    # ------------------------------------------------------------------

    def load_config(self) -> None:
        """Bootstrap the global ``kubernetes`` client config from this provider's settings.

        Resolution order:

        1. If ``config.in_cluster`` is ``True``, force in-cluster loading.
        2. If ``config.in_cluster`` is ``False``, force kubeconfig loading.
        3. Otherwise auto-detect via the in-cluster service-account sentinel.
        """
        if self._api_client is not None:
            # Pre-built client supplied; nothing to load.
            return

        try:
            if self._config.in_cluster is True:
                self._logger.debug("Loading in-cluster Kubernetes config (forced).")
                load_in_cluster_config()
            elif self._config.in_cluster is False:
                self._logger.debug("Loading kubeconfig (in_cluster=False, forced).")
                load_kubeconfig(
                    config_file=self._config.kubeconfig_path,
                    context=self._config.context,
                )
            elif is_in_cluster():
                self._logger.debug("In-cluster sentinel present; loading in-cluster config.")
                load_in_cluster_config()
            else:
                self._logger.debug(
                    "No in-cluster sentinel; loading kubeconfig (path=%s, context=%s).",
                    self._config.kubeconfig_path,
                    self._config.context,
                )
                load_kubeconfig(
                    config_file=self._config.kubeconfig_path,
                    context=self._config.context,
                )
        except KubernetesAuthError:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            raise KubernetesAuthError(f"Kubernetes config loading failed: {exc}") from exc

    # ------------------------------------------------------------------
    # API client accessors
    # ------------------------------------------------------------------

    @property
    def api_client(self) -> "ApiClient":
        """Return the underlying ``kubernetes.client.ApiClient``, building one on demand."""
        if self._api_client is None:
            self.load_config()
            try:
                from kubernetes.client import ApiClient as _ApiClient  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover — extra not installed
                raise KubernetesAuthError(
                    "kubernetes SDK is not installed; install with `pip install orb-py[kubernetes]`"
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

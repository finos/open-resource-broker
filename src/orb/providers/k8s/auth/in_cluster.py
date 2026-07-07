"""In-cluster Kubernetes config loader.

Thin wrapper around ``kubernetes.config.load_incluster_config`` used when ORB
runs inside the target cluster (or a management cluster with RBAC to the
target).  Detection uses the ``/var/run/secrets/kubernetes.io`` sentinel,
matching the upstream kubernetes client behaviour.

The wrapper exists so the ``kubernetes`` SDK import stays confined to this
package (enforced by the architecture test) and so callers can mock the
sentinel and ``load_incluster_config`` independently in unit tests.

Token refresh
-------------
In-cluster service-account tokens are projected credentials with a finite
lifetime (default 3600 seconds per the projected volume spec).  After the
token rotates on the filesystem the running process must reload it from
disk before the old token is rejected.

:class:`InClusterAuthAdapter` tracks when the config was last loaded and
exposes :meth:`refresh_if_stale` so callers (e.g. :class:`K8sClient`) can
proactively refresh before the TTL expires.  ``K8sClient`` also calls
``refresh_if_stale`` on every ``ApiException(status=401)`` response.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from orb.providers.k8s.exceptions.k8s_errors import K8sAuthError

_IN_CLUSTER_SENTINEL = Path("/var/run/secrets/kubernetes.io")

# Default refresh interval: 55 minutes.  Kubernetes rotates projected tokens
# at 80 % of their lifetime (which is 3600 s by default), so we refresh at
# 55 minutes to give a generous margin before the old token is rejected.
_DEFAULT_TOKEN_REFRESH_SECONDS: int = 55 * 60


def is_in_cluster(sentinel: Path | None = None) -> bool:
    """Return ``True`` when the in-cluster service-account secrets are present.

    Args:
        sentinel: Override path used in unit tests.  Defaults to the
            kubernetes-client canonical location.

    Returns:
        ``True`` if running inside a Kubernetes pod with a mounted service
        account, ``False`` otherwise.
    """
    path = sentinel if sentinel is not None else _IN_CLUSTER_SENTINEL
    try:
        return path.exists()
    except OSError:
        return False


def load_in_cluster_config() -> None:
    """Bootstrap the global ``kubernetes`` client config from in-cluster secrets.

    Raises:
        K8sAuthError: If the kubernetes SDK is not installed, or the
            in-cluster config cannot be loaded (e.g. missing service-account
            token).
    """
    try:
        from kubernetes import config as _k8s_config
    except ImportError as exc:  # pragma: no cover — extra not installed
        raise K8sAuthError(
            "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
        ) from exc

    try:
        _k8s_config.load_incluster_config()
    except Exception as exc:
        raise K8sAuthError(f"Failed to load in-cluster config: {exc}") from exc


class InClusterAuthAdapter:
    """Stateful wrapper around in-cluster config loading with token-refresh support.

    The adapter records the timestamp of the last successful
    ``load_incluster_config`` call and exposes :meth:`refresh_if_stale`
    so callers can proactively reload the service-account token before the
    current one expires.

    Args:
        token_refresh_seconds: How many seconds after the last load to
            trigger a refresh.  Defaults to 55 minutes (3300 s), which
            provides a comfortable margin before the token is rotated at
            80 % of its default 3600-second lifetime.
    """

    def __init__(self, token_refresh_seconds: int = _DEFAULT_TOKEN_REFRESH_SECONDS) -> None:
        self._token_refresh_seconds = token_refresh_seconds
        self._last_loaded_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Initial load
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Bootstrap the global kubernetes client config and record the timestamp."""
        load_in_cluster_config()
        self._last_loaded_at = time.monotonic()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh_if_stale(self) -> bool:
        """Re-invoke ``load_incluster_config`` when the token has aged past TTL.

        Returns:
            ``True`` if a refresh was performed, ``False`` when the token
            is still within its valid window or was never loaded (no-op
            in that case — the caller must call :meth:`load` first).

        Raises:
            K8sAuthError: When the refresh fails (e.g. the token file is
                temporarily unavailable).
        """
        if self._last_loaded_at is None:
            return False

        age = time.monotonic() - self._last_loaded_at
        if age < self._token_refresh_seconds:
            return False

        load_in_cluster_config()
        self._last_loaded_at = time.monotonic()
        return True

    # ------------------------------------------------------------------
    # Introspection helpers (for tests / health checks)
    # ------------------------------------------------------------------

    @property
    def last_loaded_at(self) -> Optional[float]:
        """Monotonic timestamp of the last successful load, or ``None``."""
        return self._last_loaded_at

    @property
    def token_age_seconds(self) -> Optional[float]:
        """Seconds since the last load, or ``None`` when never loaded."""
        if self._last_loaded_at is None:
            return None
        return time.monotonic() - self._last_loaded_at

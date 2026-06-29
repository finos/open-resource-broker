"""In-cluster Kubernetes config loader.

Thin wrapper around ``kubernetes.config.load_incluster_config`` used when ORB
runs inside the target cluster (or a management cluster with RBAC to the
target).  Detection uses the ``/var/run/secrets/kubernetes.io`` sentinel,
matching the upstream kubernetes client behaviour.

The wrapper exists so the ``kubernetes`` SDK import stays confined to this
package (enforced by the architecture test) and so callers can mock the
sentinel and ``load_incluster_config`` independently in unit tests.
"""

from __future__ import annotations

from pathlib import Path

from orb.providers.k8s.exceptions.k8s_errors import K8sAuthError

_IN_CLUSTER_SENTINEL = Path("/var/run/secrets/kubernetes.io")


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
        from kubernetes import config as _k8s_config  # noqa: PLC0415 — confined to this module
    except ImportError as exc:  # pragma: no cover — extra not installed
        raise K8sAuthError(
            "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
        ) from exc

    try:
        _k8s_config.load_incluster_config()
    except Exception as exc:
        raise K8sAuthError(f"Failed to load in-cluster config: {exc}") from exc

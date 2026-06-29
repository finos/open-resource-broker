"""kubeconfig-based Kubernetes config loader.

Thin wrapper around ``kubernetes.config.load_kube_config`` for the
out-of-cluster case.  Keeps the ``kubernetes`` SDK import confined to this
package and exposes a small, unit-testable seam.
"""

from __future__ import annotations

from typing import Optional

from orb.providers.k8s.exceptions.k8s_errors import K8sAuthError


def load_kubeconfig(
    config_file: Optional[str] = None,
    context: Optional[str] = None,
) -> None:
    """Bootstrap the global ``kubernetes`` client config from a kubeconfig file.

    Args:
        config_file: Path to the kubeconfig file.  When ``None`` the
            kubernetes client falls back to the ``KUBECONFIG`` env var and
            then the default ``~/.kube/config`` location.
        context: Name of the context to activate.  When ``None`` the
            current context from the kubeconfig is used.

    Raises:
        K8sAuthError: If the kubernetes SDK is not installed or the
            kubeconfig cannot be loaded (e.g. missing file, unknown context).
    """
    try:
        from kubernetes import config as _k8s_config  # noqa: PLC0415 — confined to this module
    except ImportError as exc:  # pragma: no cover — extra not installed
        raise K8sAuthError(
            "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
        ) from exc

    try:
        _k8s_config.load_kube_config(config_file=config_file, context=context)
    except Exception as exc:
        raise K8sAuthError(
            f"Failed to load kubeconfig (config_file={config_file!r}, context={context!r}): {exc}"
        ) from exc

"""Kubernetes provider defaults loader.

The Kubernetes provider currently ships an empty defaults bundle — it has
no provider-level defaults yet.  When template-defaults JSON is later
added under ``orb/providers/k8s/config/`` this loader can read it from
``importlib.resources`` exactly like the AWS counterpart in
:mod:`orb.providers.aws.defaults_loader`.
"""

from __future__ import annotations

from orb.domain.base.ports.provider_defaults_loader_port import ProviderDefaultsLoaderPort


class KubernetesDefaultsLoader:
    """Return the Kubernetes provider's defaults bundle.

    Satisfies :class:`~orb.domain.base.ports.provider_defaults_loader_port.ProviderDefaultsLoaderPort`.
    """

    def load_defaults(self) -> dict:
        """Return Kubernetes provider defaults.

        Returns:
            Empty dict.  Populated once a bundled
            ``kubernetes_defaults.json`` is shipped alongside the package.
        """
        return {}


# Runtime check that KubernetesDefaultsLoader satisfies the protocol
assert isinstance(KubernetesDefaultsLoader(), ProviderDefaultsLoaderPort)

"""Kubernetes provider defaults loader.

Phase A ships an empty defaults bundle — the Kubernetes provider has no
provider-level defaults yet.  Phase G introduces template-defaults JSON
under ``orb/providers/k8s/config/`` and this loader reads from
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
            Empty dict in Phase A; populated in Phase G when the bundled
            ``kubernetes_defaults.json`` lands.
        """
        return {}


# Runtime check that KubernetesDefaultsLoader satisfies the protocol
assert isinstance(KubernetesDefaultsLoader(), ProviderDefaultsLoaderPort)

"""Kubernetes provider defaults loader."""

from __future__ import annotations

import json

from orb.domain.base.ports.provider_defaults_loader_port import ProviderDefaultsLoaderPort


class KubernetesDefaultsLoader:
    """Loads defaults from the bundled ``k8s_defaults.json`` config file.

    Satisfies :class:`~orb.domain.base.ports.provider_defaults_loader_port.ProviderDefaultsLoaderPort`.
    """

    def load_defaults(self) -> dict:
        """Return Kubernetes provider defaults from the bundled ``k8s_defaults.json``.

        Returns:
            Raw configuration dictionary contributed by the Kubernetes provider.
            Returns an empty dict if the file cannot be read.
        """
        try:
            from importlib.resources import files

            text = (
                files("orb.providers.k8s.config")
                .joinpath("k8s_defaults.json")
                .read_text(encoding="utf-8")
            )
            return json.loads(text)
        except Exception:
            return {}


assert isinstance(KubernetesDefaultsLoader(), ProviderDefaultsLoaderPort)

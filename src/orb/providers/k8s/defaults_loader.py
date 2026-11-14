"""Kubernetes provider defaults loader."""

from __future__ import annotations

import json

from orb.domain.base.ports.provider_defaults_loader_port import ProviderDefaultsLoaderPort
from orb.providers.k8s.handler_plugins import plugin_handler_defaults


class KubernetesDefaultsLoader:
    """Loads defaults from the bundled ``k8s_defaults.json`` config file.

    Satisfies :class:`~orb.domain.base.ports.provider_defaults_loader_port.ProviderDefaultsLoaderPort`.
    """

    def load_defaults(self) -> dict:
        """Return Kubernetes provider defaults from the bundled ``k8s_defaults.json``.

        Returns:
            Raw configuration dictionary contributed by the Kubernetes provider,
            with a ``handlers`` entry for each registered plugin handler.
            Returns only the plugin entries if the file cannot be read.
        """
        try:
            from importlib.resources import files

            text = (
                files("orb.providers.k8s.config")
                .joinpath("k8s_defaults.json")
                .read_text(encoding="utf-8")
            )
            defaults = json.loads(text)
        except Exception:
            # Defaults file is optional; missing or unreadable content
            # falls back to an empty dict so callers can proceed.
            defaults = {}
        return _with_plugin_handlers(defaults)


def _with_plugin_handlers(defaults: dict) -> dict:
    """Add a ``handlers`` entry for each plugin registered with ``register_k8s_handler``.

    The generic template validation reads the supported APIs from these
    entries, so a registered plugin API must appear here.  Entries already
    present in the defaults file win.
    """
    plugins = plugin_handler_defaults()
    if not plugins:
        return defaults
    k8s = (
        defaults.setdefault("provider", {})
        .setdefault("provider_defaults", {})
        .setdefault("k8s", {})
    )
    handlers = k8s.setdefault("handlers", {})
    for api, entry in plugins.items():
        handlers.setdefault(api, entry)
    return defaults


assert isinstance(  # noqa: S101  # module-level protocol-conformance smoke check, also enforced statically by pyright
    KubernetesDefaultsLoader(), ProviderDefaultsLoaderPort
)

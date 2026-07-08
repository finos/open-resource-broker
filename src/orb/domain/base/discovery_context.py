"""Typed context passed to provider infrastructure-discovery methods."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DiscoveryContext:
    """Immutable context passed to infrastructure discovery routines.

    Attributes:
        provider_type: The provider type identifier (e.g. ``"k8s"``, ``"aws"``).
        provider_config: Opaque provider-specific configuration dict.  AWS callers
            store ``region`` and ``profile`` here; k8s callers store kubeconfig
            context names and similar.  Provider-agnostic layers must not inspect
            the contents of this dict.
    """

    provider_type: str
    provider_config: dict[str, Any] = field(default_factory=dict)


def discovery_context_from_dict(raw: dict) -> DiscoveryContext:
    """Build a :class:`DiscoveryContext` from a raw provider-config dict.

    Extracts ``provider_type`` from the top-level ``type`` or ``provider_type``
    key and forwards the nested ``config`` section (plus any remaining
    top-level provider-specific keys) into ``provider_config``.

    Args:
        raw: Provider config dict as passed by the strategy layer.

    Returns:
        A fully typed :class:`DiscoveryContext`.
    """
    provider_type: str = raw.get("type", raw.get("provider_type", ""))
    config_section: dict = raw.get("config", {}) or {}
    # Merge the nested config section with any remaining provider-specific keys
    # so that callers that embed region/profile at the top level still work.
    provider_config: dict[str, Any] = {
        k: v for k, v in raw.items() if k not in ("type", "provider_type", "config")
    }
    provider_config.update(config_section)
    return DiscoveryContext(provider_type=provider_type, provider_config=provider_config)

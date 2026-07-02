"""Typed context passed to provider infrastructure-discovery methods."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DiscoveryContext:
    """Immutable context passed to infrastructure discovery routines.

    Attributes:
        provider_type: The provider type identifier (e.g. ``"k8s"``, ``"aws"``).
        region: Cloud or cluster region string.  Empty string when not applicable.
        profile: Credential-source identifier chosen during ``orb init``.
            For the k8s provider this is either a kubeconfig context name or
            the literal ``"in_cluster"`` sentinel.  ``None`` when no
            credential-source was pre-selected.
    """

    provider_type: str
    region: str = ""
    profile: Optional[str] = None


def discovery_context_from_dict(raw: dict) -> DiscoveryContext:
    """Build a :class:`DiscoveryContext` from a raw provider-config dict.

    Reads ``raw["config"]["profile"]`` and ``raw.get("region", "")`` so
    callers that still receive the legacy dict contract do not need to be
    changed before the typed variant is adopted end-to-end.

    Args:
        raw: Provider config dict as passed by the strategy layer.

    Returns:
        A fully typed :class:`DiscoveryContext`.
    """
    config_section: dict = raw.get("config", {}) or {}
    provider_type: str = raw.get("type", raw.get("provider_type", ""))
    region: str = raw.get("region", "") or ""
    profile: Optional[str] = config_section.get("profile")
    return DiscoveryContext(provider_type=provider_type, region=region, profile=profile)

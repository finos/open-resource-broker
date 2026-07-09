"""GCP provider defaults loader."""

from __future__ import annotations

from orb.domain.base.ports.provider_defaults_loader_port import ProviderDefaultsLoaderPort
from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy


class GCPDefaultsLoader:
    """Load defaults exposed by the GCP provider strategy."""

    def load_defaults(self) -> dict:
        """Return GCP provider defaults."""
        return GCPProviderStrategy.get_defaults_config()


assert isinstance(GCPDefaultsLoader(), ProviderDefaultsLoaderPort)

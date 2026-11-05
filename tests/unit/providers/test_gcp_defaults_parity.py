"""The GCP defaults loader must report what the strategy reports."""

from __future__ import annotations

import pytest

pytest.importorskip("google.api_core")


@pytest.mark.unit
def test_defaults_loader_matches_strategy_defaults() -> None:
    from orb.providers.gcp.defaults_loader import GCPDefaultsLoader
    from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy

    assert GCPDefaultsLoader().load_defaults() == GCPProviderStrategy.get_defaults_config()

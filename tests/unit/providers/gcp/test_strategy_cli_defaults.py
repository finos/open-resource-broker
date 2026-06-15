"""Tests for GCP strategy CLI config separation."""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock

from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy


def _strategy() -> GCPProviderStrategy:
    return GCPProviderStrategy(
        config=GCPProviderConfig(project_id="orb-example-12345", region="us-central1"),
        logger=MagicMock(),
        provider_name="gcp-default",
    )


def test_gcp_strategy_cli_infrastructure_defaults_extracts_service_account_fields() -> None:
    strategy = _strategy()

    result = strategy.get_cli_infrastructure_defaults(
        Namespace(
            gcp_network="default",
            gcp_subnetwork="default-subnet",
            gcp_service_account_email="orb@example.iam.gserviceaccount.com",
            gcp_service_account_scopes=(
                "https://www.googleapis.com/auth/compute.readonly,"
                "https://www.googleapis.com/auth/devstorage.read_only"
            ),
        )
    )

    assert result == {
        "network": "default",
        "subnetwork": "default-subnet",
        "service_account_email": "orb@example.iam.gserviceaccount.com",
        "service_account_scopes": [
            "https://www.googleapis.com/auth/compute.readonly",
            "https://www.googleapis.com/auth/devstorage.read_only",
        ],
    }

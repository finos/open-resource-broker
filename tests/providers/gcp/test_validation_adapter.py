"""Tests for the GCP provider validation adapter."""

from unittest.mock import MagicMock

from orb.providers.gcp.capabilities import get_supported_apis
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.infrastructure.adapters.gcp_validation_adapter import (
    GCPValidationAdapter,
)


def test_supported_provider_apis_come_from_gcp_capabilities() -> None:
    adapter = GCPValidationAdapter(
        config=GCPProviderConfig(
            project_id="orb-example-12345",
            region="us-central1",
            zones=["us-central1-a"],
        ),
        logger=MagicMock(),
    )
    supported_apis = sorted(get_supported_apis())

    assert adapter.get_supported_provider_apis() == supported_apis
    assert all(adapter.validate_provider_api(api) for api in supported_apis)
    assert adapter.validate_provider_api("UnsupportedAPI") is False

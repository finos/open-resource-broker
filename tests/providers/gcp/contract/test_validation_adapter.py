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


def _adapter() -> GCPValidationAdapter:
    return GCPValidationAdapter(
        config=GCPProviderConfig(
            project_id="orb-example-12345", region="us-central1", zones=["us-central1-a"]
        ),
        logger=MagicMock(),
    )


_VALID_ROW = {
    "template_id": "t1",
    "provider_api": "MIG",
    "max_machines": 2,
    "machine_type": "n2-standard-2",
    "project_id": "orb-example-12345",
    "region": "us-central1",
    "zones": ["us-central1-a", "us-central1-b"],
    "source_image": "projects/p/global/images/i",
}


def test_validate_reports_mig_without_machines() -> None:
    result = _adapter().validate({**_VALID_ROW, "max_machines": 0})

    assert any("max_machines" in error for error in result["errors"])


def test_validate_reports_single_vm_without_zone() -> None:
    result = _adapter().validate({**_VALID_ROW, "provider_api": "SingleVM", "max_machines": 1})

    assert any("exactly one explicit zone" in error for error in result["errors"])


def test_validate_passes_a_valid_template() -> None:
    result = _adapter().validate(_VALID_ROW)

    assert result["errors"] == []

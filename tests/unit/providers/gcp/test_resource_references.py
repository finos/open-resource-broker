"""Tests for GCP resource reference normalization."""

from orb.providers.gcp.infrastructure.handlers.base_handler import _is_gcp_resource_reference


def test_gcp_resource_reference_accepts_paths_and_https_self_links() -> None:
    assert _is_gcp_resource_reference("global/networks/default") is True
    assert _is_gcp_resource_reference("regions/us-central1/subnetworks/default") is True
    assert _is_gcp_resource_reference("projects/orb/global/networks/default") is True
    assert _is_gcp_resource_reference("https://www.googleapis.com/compute/v1/projects/orb") is True


def test_gcp_resource_reference_rejects_plain_http_self_links() -> None:
    assert _is_gcp_resource_reference("http://www.googleapis.com/compute/v1/projects/orb") is False

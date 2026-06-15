"""Unit tests for the GCP CLI spec."""

from argparse import Namespace

from orb.providers.gcp.cli.gcp_cli_spec import GCPCLISpec


def test_validate_add_requires_project_and_region() -> None:
    spec = GCPCLISpec()

    errors = spec.validate_add(Namespace(gcp_project_id=None, gcp_region=None))

    assert "--gcp-project-id is required" in errors
    assert "--gcp-region is required" in errors


def test_extract_config_returns_gcp_provider_config_fields_only() -> None:
    spec = GCPCLISpec()

    config = spec.extract_config(
        Namespace(
            gcp_project_id="orb-example-12345",
            gcp_region="us-central1",
            gcp_zones="us-central1-a,us-central1-b",
            gcp_network="default",
            gcp_subnetwork="default-subnet",
            gcp_service_account_email="orb@example.iam.gserviceaccount.com",
            gcp_service_account_scopes=(
                "https://www.googleapis.com/auth/compute.readonly,"
                "https://www.googleapis.com/auth/devstorage.read_only"
            ),
        )
    )

    assert config["project_id"] == "orb-example-12345"
    assert config["region"] == "us-central1"
    assert config["zones"] == ["us-central1-a", "us-central1-b"]
    assert "service_account_email" not in config
    assert "service_account_scopes" not in config
    assert "use_application_default_credentials" not in config


def test_generate_name_uses_project_and_region() -> None:
    spec = GCPCLISpec()

    name = spec.generate_name(
        Namespace(gcp_project_id="orb-example-12345", gcp_region="us-central1")
    )

    assert name == "gcp_orb-example-12345_us-central1"

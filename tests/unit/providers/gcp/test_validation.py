"""Unit tests for GCP template validation."""

from orb.providers.gcp.configuration.validator import validate_gcp_template


def test_validate_gcp_template_accepts_regional_mig() -> None:
    result = validate_gcp_template(
        {
            "template_id": "gcp-mig",
            "provider_type": "gcp",
            "provider_api": "MIG",
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "zones": ["us-central1-a", "us-central1-b"],
            "mig_scope": "regional",
            "instance_type": "e2-standard-4",
            "max_instances": 3,
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
        }
    )

    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_gcp_template_rejects_singlevm_with_multiple_instances() -> None:
    result = validate_gcp_template(
        {
            "template_id": "gcp-singlevm",
            "provider_type": "gcp",
            "provider_api": "SingleVM",
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "zones": ["us-central1-a"],
            "instance_type": "e2-standard-4",
            "max_instances": 2,
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
        }
    )

    assert result["valid"] is False
    assert any("SingleVM templates require max_instances == 1" in error for error in result["errors"])


def test_validate_gcp_template_rejects_singlevm_without_explicit_zone() -> None:
    result = validate_gcp_template(
        {
            "template_id": "gcp-singlevm",
            "provider_type": "gcp",
            "provider_api": "SingleVM",
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "instance_type": "e2-standard-4",
            "max_instances": 1,
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
        }
    )

    assert result["valid"] is False
    assert any(
        "SingleVM templates require exactly one explicit zone" in error
        for error in result["errors"]
    )


def test_validate_gcp_template_rejects_boot_disk_type_reference() -> None:
    result = validate_gcp_template(
        {
            "template_id": "gcp-mig",
            "provider_type": "gcp",
            "provider_api": "MIG",
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "zones": ["us-central1-a", "us-central1-b"],
            "mig_scope": "regional",
            "instance_type": "e2-standard-4",
            "max_instances": 3,
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
            "boot_disk_type": "zones/us-central1-a/diskTypes/pd-balanced",
        }
    )

    assert result["valid"] is False
    assert any("boot_disk_type must be a disk type resource name" in error for error in result["errors"])

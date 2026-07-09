"""Unit tests for GCP template validation."""

import pytest

from orb.infrastructure.template.dtos import TemplateDTO
from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
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


def test_gcp_template_dto_roundtrip_preserves_provider_fields() -> None:
    original = GCPTemplate(
        template_id="gcp-singlevm",
        provider_api="SingleVM",
        project_id="orb-example-12345",
        region="us-central1",
        zones=["us-central1-a"],
        instance_type="e2-standard-4",
        max_instances=1,
        source_image_family="debian-12",
        source_image_project="debian-cloud",
        network="projects/orb-example-12345/global/networks/default",
        subnetwork="regions/us-central1/subnetworks/default",
        service_account_email="worker@orb-example-12345.iam.gserviceaccount.com",
        service_account_scopes=["https://www.googleapis.com/auth/compute"],
        labels={"component": "worker"},
        network_tags=["ssh"],
        provisioning_model="STANDARD",
        boot_disk_type="pd-balanced",
        boot_disk_size_gb=64,
        instance_template_name_prefix="orb-worker",
    )

    dto = TemplateDTO.from_domain(original)

    assert isinstance(dto.provider_config, GCPTemplateExtensionConfig)
    provider_config = dto.provider_config.model_dump(exclude_none=True, exclude_unset=True)
    assert provider_config["project_id"] == "orb-example-12345"
    assert provider_config["region"] == "us-central1"
    assert provider_config["zones"] == ["us-central1-a"]
    assert provider_config["service_account_email"] == (
        "worker@orb-example-12345.iam.gserviceaccount.com"
    )
    assert provider_config["labels"] == {"component": "worker"}

    restored_config = dto.model_dump(exclude={"provider_config"}, exclude_none=True)
    restored_config.update(dto.provider_config.to_template_defaults())
    restored = GCPTemplate.model_validate(restored_config)
    assert restored.project_id.value == "orb-example-12345"
    assert restored.region.value == "us-central1"
    assert [zone.value for zone in restored.zones] == ["us-central1-a"]
    assert restored.service_account_email == "worker@orb-example-12345.iam.gserviceaccount.com"
    assert restored.labels == {"component": "worker"}
    assert restored.network_tags == ["ssh"]
    assert restored.boot_disk_size_gb == 64


def test_gcp_template_rejects_invalid_provider_config_type() -> None:
    with pytest.raises(ValueError):
        GCPTemplate.model_validate(
            {
                "template_id": "gcp-invalid-provider-config",
                "provider_type": "gcp",
                "provider_api": "SingleVM",
                "project_id": "orb-example-12345",
                "region": "us-central1",
                "zones": ["us-central1-a"],
                "instance_type": "e2-standard-4",
                "max_instances": 1,
                "source_image_family": "debian-12",
                "source_image_project": "debian-cloud",
                "provider_config": "not-a-provider-config",
            }
        )


def test_gcp_template_accepts_provider_config_extension_payload() -> None:
    template = GCPTemplate.model_validate(
        {
            "template_id": "gcp-mig",
            "provider_type": "gcp",
            "max_instances": 3,
            "provider_config": {
                "provider_api": "MIG",
                "machine_type": "e2-standard-4",
                "project_id": "orb-example-12345",
                "region": "us-central1",
                "zones": ["us-central1-a", "us-central1-b"],
                "mig_scope": "regional",
                "source_image_family": "debian-12",
                "source_image_project": "debian-cloud",
                "instance_template_name_prefix": "orb",
            },
        }
    )

    assert template.provider_api == "MIG"
    assert template.instance_type == "e2-standard-4"
    assert template.project_id.value == "orb-example-12345"
    assert [zone.value for zone in template.zones] == ["us-central1-a", "us-central1-b"]
    assert template.instance_template_name_prefix == "orb"

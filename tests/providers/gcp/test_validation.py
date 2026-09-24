"""Unit tests for GCP template validation."""

import pytest

from orb.application.dto.template import TemplateDTO
from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig
from orb.providers.gcp.configuration.validator import validate_gcp_template
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate


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


def test_gcp_template_translates_standard_template_fields() -> None:
    dto = TemplateDTO(
        template_id="gcp-standard",
        provider_type="gcp",
        provider_api="SingleVM",
        image_id="projects/debian-cloud/global/images/debian-12-v1",
        machine_types={"e2-micro": 1},
        network_zones=["us-central1-a"],
        root_device_volume_size=20,
        volume_type="pd-standard",
        tags={"purpose": "worker"},
    )

    template = GCPTemplate.model_validate(
        {**dto.model_dump(), "project_id": "orb-example-12345", "region": "us-central1"}
    )

    assert template.machine_type == "e2-micro"
    assert template.source_image == "projects/debian-cloud/global/images/debian-12-v1"
    assert [str(zone) for zone in template.zones] == ["us-central1-a"]
    assert template.boot_disk_size_gb == 20
    assert str(template.boot_disk_type) == "pd-standard"
    assert template.labels == {"purpose": "worker"}


def test_gcp_template_rejects_multiple_standard_machine_types() -> None:
    result = validate_gcp_template(
        {
            "template_id": "gcp-ambiguous",
            "provider_api": "MIG",
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "machine_types": {"e2-micro": 1, "e2-small": 1},
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
        }
    )
    assert not result["valid"]
    assert any("one machine type with weight 1" in error for error in result["errors"])


def test_gcp_template_rejects_weighted_standard_machine_type() -> None:
    result = validate_gcp_template(
        {
            "template_id": "gcp-weighted",
            "provider_api": "MIG",
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "machine_types": {"e2-micro": 2},
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
        }
    )
    assert not result["valid"]
    assert any("one machine type with weight 1" in error for error in result["errors"])


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
    assert any(
        "SingleVM templates require max_machines == 1" in error for error in result["errors"]
    )


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


def test_validate_gcp_template_rejects_named_ssh_key_pair() -> None:
    result = validate_gcp_template(
        {
            "template_id": "gcp-singlevm",
            "provider_type": "gcp",
            "provider_api": "SingleVM",
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "zones": ["us-central1-a"],
            "instance_type": "e2-standard-4",
            "max_instances": 1,
            "key_name": "operator-key",
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
        }
    )

    assert result["valid"] is False
    assert any("key_name is unsupported" in error for error in result["errors"])


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
    assert any(
        "boot_disk_type must be a disk type resource name" in error for error in result["errors"]
    )


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
        boot_disk_type="pd-balanced",
        boot_disk_size_gb=64,
        instance_template_name_prefix="orb-worker",
    )

    dto = TemplateDTOFactory().from_domain(original)
    assert dto.root_device_volume_size == 64
    assert dto.volume_type == "pd-balanced"

    provider_payload = dto.to_dict()
    assert GCPTemplate.model_validate(provider_payload).machine_type == "e2-standard-4"

    assert isinstance(dto.provider_config, GCPTemplateExtensionConfig)
    provider_config = dto.provider_config.model_dump(exclude_none=True, exclude_unset=True)
    assert provider_config["project_id"] == "orb-example-12345"
    assert provider_config["region"] == "us-central1"
    assert provider_config["zones"] == ["us-central1-a"]
    assert provider_config["service_account_email"] == (
        "worker@orb-example-12345.iam.gserviceaccount.com"
    )
    assert provider_config["labels"] == {"component": "worker"}

    restored_config = dto.model_dump(exclude_none=True)
    restored_config.pop("provider_config")
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


def test_gcp_template_rejects_gcp_api_provisioning_model_input() -> None:
    with pytest.raises(ValueError):
        GCPTemplate.model_validate(
            {
                "template_id": "gcp-provisioning-model-input",
                "provider_type": "gcp",
                "provider_api": "MIG",
                "project_id": "orb-example-12345",
                "region": "us-central1",
                "zones": ["us-central1-a", "us-central1-b"],
                "mig_scope": "regional",
                "instance_type": "e2-standard-4",
                "max_instances": 2,
                "provisioning_model": "STANDARD",
                "source_image_family": "debian-12",
                "source_image_project": "debian-cloud",
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
                "price_type": "spot",
                "source_image_family": "debian-12",
                "source_image_project": "debian-cloud",
                "instance_template_name_prefix": "orb",
            },
        }
    )

    assert template.provider_api == "MIG"
    assert template.machine_type == "e2-standard-4"
    assert template.price_type == "spot"
    assert template.project_id.value == "orb-example-12345"
    assert [zone.value for zone in template.zones] == ["us-central1-a", "us-central1-b"]
    assert template.instance_template_name_prefix == "orb"

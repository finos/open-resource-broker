"""Unit tests for OCI template-to-payload mapping helpers."""

import json
from pathlib import Path

from orb.providers.oci.mapping.template_mapper import OCITemplateMapper
from orb.providers.oci.services import OCIPricingService


def test_validate_required_fields_reports_missing() -> None:
    missing = OCITemplateMapper.validate_required_fields({"template_id": "tpl-oci"})
    assert "image_id" in missing
    assert "shape" in missing
    assert "subnet_id" in missing
    assert "compartment_id" in missing


def test_build_launch_payload_maps_expected_fields() -> None:
    template = {
        "template_id": "tpl-oci",
        "image_id": "ocid1.image.oc1..img",
        "instance_type": "VM.Standard.E4.Flex",
        "subnet_ids": ["ocid1.subnet.oc1..subnet"],
        "security_group_ids": ["ocid1.nsg.oc1..nsg"],
        "compartment_id": "ocid1.compartment.oc1..compartment",
        "user_data": "IyEvYmluL2Jhc2g=",
        "tags": {"env": "dev"},
    }

    payload = OCITemplateMapper.build_launch_payload(template, display_name="tpl-oci-1")

    assert payload["display_name"] == "tpl-oci-1"
    assert payload["shape"] == "VM.Standard.E4.Flex"
    assert payload["source_details"]["image_id"] == "ocid1.image.oc1..img"
    assert payload["create_vnic_details"]["subnet_id"] == "ocid1.subnet.oc1..subnet"
    assert payload["create_vnic_details"]["nsg_ids"] == ["ocid1.nsg.oc1..nsg"]
    assert payload["metadata"]["user_data"] == "IyEvYmluL2Jhc2g="
    assert payload["freeform_tags"]["env"] == "dev"


def test_normalize_template_fields_reads_nested_configuration() -> None:
    template = {
        "template_id": "tpl-oci",
        "image_id": "ocid1.image.oc1..img",
        "configuration": {
            "instance_type": "VM.Standard.E4.Flex",
            "subnet_ids": ["ocid1.subnet.oc1..subnet"],
            "compartment_id": "ocid1.compartment.oc1..compartment",
            "security_group_ids": [],
        },
    }

    missing = OCITemplateMapper.validate_required_fields(template)

    assert missing == []


def test_normalize_template_fields_reads_machine_types_and_metadata() -> None:
    template = {
        "template_id": "tpl-oci",
        "image_id": "ocid1.image.oc1..img",
        "machine_types": {"VM.Standard.E6.Flex": 1},
        "subnet_ids": ["ocid1.subnet.oc1..subnet"],
        "metadata": {"compartment_id": "ocid1.compartment.oc1..compartment"},
    }

    missing = OCITemplateMapper.validate_required_fields(template)

    assert missing == []


def test_build_launch_payload_supports_flex_sizing_inputs() -> None:
    template = {
        "template_id": "tpl-flex",
        "image_id": "ocid1.image.oc1..img",
        "instance_type": "VM.Standard.E6.Flex",
        "subnet_ids": ["ocid1.subnet.oc1..subnet"],
        "compartment_id": "ocid1.compartment.oc1..compartment",
        "ocpus": 2,
        "memory_gbs": 16,
        "boot_volume_gbs": 100,
        "capacity_type": "preemptible",
    }

    payload = OCITemplateMapper.build_launch_payload(template, display_name="tpl-flex-1")

    assert payload["shape_config"]["ocpus"] == 2
    assert payload["shape_config"]["memoryInGBs"] == 16
    assert payload["boot_volume_gbs"] == 100
    assert payload["capacity_type"] == "preemptible"
    assert payload["preemptible_instance_config"]["preemptionAction"]["type"] == "TERMINATE"
    assert payload["pricing_estimate"]["total_hourly"] > 0
    assert payload["pricing_estimate"]["estimated"] is True


def test_validate_required_fields_rejects_bm_flex_override() -> None:
    template = {
        "template_id": "tpl-bm",
        "image_id": "ocid1.image.oc1..img",
        "shape": "BM.Standard.E5.192",
        "subnet_ids": ["ocid1.subnet.oc1..subnet"],
        "compartment_id": "ocid1.compartment.oc1..compartment",
        "ocpus": 32,
    }

    missing = OCITemplateMapper.validate_required_fields(template)

    assert "bm_shape_does_not_support_flex_sizing" in missing


def test_estimate_hourly_cost_uses_preemptible_discount() -> None:
    template = {
        "template_id": "tpl-cost",
        "image_id": "ocid1.image.oc1..img",
        "instance_type": "VM.Standard.E6.Flex",
        "subnet_ids": ["ocid1.subnet.oc1..subnet"],
        "compartment_id": "ocid1.compartment.oc1..compartment",
        "ocpus": 2,
        "memory_gbs": 16,
        "boot_volume_gbs": 100,
    }
    ondemand = dict(template)
    preemptible = dict(template)
    preemptible["capacity_type"] = "preemptible"

    ondemand_estimate = OCIPricingService.estimate_hourly_cost(ondemand)
    preemptible_estimate = OCIPricingService.estimate_hourly_cost(preemptible)

    assert preemptible_estimate["total_hourly"] < ondemand_estimate["total_hourly"]
    assert preemptible_estimate["rate_source"] == "oci_static_reference_rates"


def test_pricing_unknown_for_unsupported_shape() -> None:
    estimate = OCIPricingService.estimate_hourly_cost(
        {
            "template_id": "tpl-unknown",
            "image_id": "ocid1.image.oc1..img",
            "instance_type": "VM.Standard.Unknown.Flex",
            "subnet_ids": ["ocid1.subnet.oc1..subnet"],
            "compartment_id": "ocid1.compartment.oc1..compartment",
            "ocpus": 2,
            "memory_gbs": 16,
            "boot_volume_gbs": 100,
        }
    )

    assert estimate["total_hourly"] is None
    assert estimate["compute_hourly"] is None
    assert estimate["confidence"] == "unknown"
    assert estimate["warnings"]


def test_pricing_override_supports_custom_shape_rate() -> None:
    estimate = OCIPricingService.estimate_hourly_cost(
        {
            "template_id": "tpl-custom",
            "image_id": "ocid1.image.oc1..img",
            "instance_type": "VM.Standard.Custom.Flex",
            "subnet_ids": ["ocid1.subnet.oc1..subnet"],
            "compartment_id": "ocid1.compartment.oc1..compartment",
            "ocpus": 2,
            "memory_gbs": 10,
            "pricing": {
                "overrides": {
                    "shape_family_rates": {
                        "VM.Standard.Custom.Flex": {
                            "ocpu_hourly": 0.01,
                            "memory_gb_hourly": 0.001,
                        }
                    }
                }
            },
        }
    )

    assert estimate["total_hourly"] == 0.03
    assert estimate["rate_source"] == "template_pricing_override"
    assert estimate["confidence"] == "medium"


def test_oci_template_examples_are_duplicate_free_and_defaultable() -> None:
    root = Path(__file__).parents[4]
    templates_data = json.loads((root / "config/oci_templates.json").read_text(encoding="utf-8"))
    remote_config = json.loads(
        (root / "config/oci_config.remote.example.json").read_text(encoding="utf-8")
    )
    defaults = remote_config["provider"]["provider_defaults"]["oci"]["template_defaults"]

    seen_ids = set()
    for template in templates_data["templates"]:
        template_id = template["template_id"]
        assert template_id not in seen_ids
        seen_ids.add(template_id)
        assert "allocation_strategy" not in template
        assert "priceType" not in template
        assert "maxSpotPrice" not in template

        merged = {**defaults, **template}
        assert OCITemplateMapper.validate_required_fields(merged) == []


def test_oci_bm_template_does_not_include_flex_sizing_fields() -> None:
    root = Path(__file__).parents[4]
    data = json.loads((root / "config/oci_templates.json").read_text(encoding="utf-8"))
    bm_template = next(t for t in data["templates"] if t["template_id"] == "oci-bm-standard-ondemand")

    assert "shape_config" not in bm_template
    assert "ocpus" not in bm_template
    assert "memory_gbs" not in bm_template


def test_oci_config_examples_make_credential_modes_explicit() -> None:
    root = Path(__file__).parents[4]
    remote = json.loads((root / "config/oci_config.remote.example.json").read_text(encoding="utf-8"))
    local = json.loads((root / "config/oci_config.local.example.json").read_text(encoding="utf-8"))

    remote_provider = remote["provider"]["providers"][0]
    local_provider = local["provider"]["providers"][0]

    assert remote_provider["config"]["credential_source"] == "instance_principal"
    assert "profile" not in remote_provider["config"]
    assert local_provider["config"]["credential_source"] == "profile"
    assert local_provider["config"]["profile"] == "DEFAULT"

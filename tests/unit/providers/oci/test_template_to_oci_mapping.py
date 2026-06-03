"""Unit tests for OCI template-to-payload mapping helpers."""

from orb.providers.oci.mapping.template_mapper import OCITemplateMapper


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

    ondemand_estimate = OCITemplateMapper.estimate_hourly_cost(ondemand)
    preemptible_estimate = OCITemplateMapper.estimate_hourly_cost(preemptible)

    assert preemptible_estimate["total_hourly"] < ondemand_estimate["total_hourly"]

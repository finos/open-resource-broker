"""Catalog sizing and architecture survive persistence and scheduler mapping."""

import json
from importlib.resources import files
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.scheduler.hostfactory.field_mapper import HostFactoryFieldMapper
from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.domain.template import OCITemplate
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.mapping.template_mapper import OCITemplateMapper
from orb.providers.oci.provider_plugin import OCIPlugin

CATALOG = json.loads(
    files("orb.providers.oci.config").joinpath("oci_templates.json").read_text(encoding="utf-8")
)["templates"]
EXPECTED_SHAPES = {
    "VM.Standard.E5.Flex",
    "VM.Standard.E6.Flex",
    "VM.Standard.E6.Ax.Flex",
    "VM.Standard3.Flex",
    "VM.Standard4.Ax.Flex",
    "VM.Optimized3.Flex",
    "VM.Standard.A1.Flex",
    "VM.Standard.A2.Flex",
    "VM.Standard.A4.Flex",
    "VM.Standard.A4.Ax.Flex",
    "VM.DenseIO.E5.Flex",
    "VM.DenseIO.E6.Ax.Flex",
    "BM.Standard.E5.192",
    "BM.Standard.E6.256",
    "BM.Standard.E6.Ax.192",
    "BM.Standard3.64",
    "BM.Standard4.Ax.120",
    "BM.Standard.A1.160",
    "BM.Standard.A4.48",
    "BM.Standard.A4.Ax.48",
    "BM.DenseIO.E5.128",
    "BM.DenseIO.E6.Ax.192",
    "BM.Optimized3.36",
}


@pytest.fixture
def plugin():
    OCIPlugin.reset_for_testing()
    plugin = OCIPlugin()
    plugin.initialize_provider()
    plugin.register_services_with_di(MagicMock())
    yield plugin
    OCIPlugin.reset_for_testing()


def test_packaged_catalog_covers_requested_families():
    assert {shape for t in CATALOG for shape in t["machine_types"]} == EXPECTED_SHAPES
    assert len({t["template_id"] for t in CATALOG}) == len(CATALOG) == 25
    assert all(len(t["machine_types"]) == 1 for t in CATALOG)


@pytest.mark.parametrize("example", CATALOG, ids=lambda t: t["template_id"])
def test_catalog_roundtrip_to_launch_and_hostfactory(plugin, example):
    source = {
        "image_id": "x86-default-image",
        "subnet_ids": ["subnet-1"],
        "security_group_ids": ["nsg-1"],
        "user_data": "IyEvYmluL2Jhc2g=",
        "compartment_id": "compartment-1",
        "availability_domain": "AD-1",
        **example,
    }
    domain = OCITemplate(**source)
    # JSON save/reload must retain extensions, including sizing and CPU vendor.
    saved = TemplateDTOFactory().from_domain(domain).to_dict()
    saved = json.loads(json.dumps(saved, default=str))
    reloaded = TemplateDTOFactory().from_domain(OCITemplate(**saved)).to_dict()
    hf = HostFactoryFieldMapper("oci")
    mapped = hf.map_input_fields(hf.map_output_fields(reloaded))
    for template in (reloaded, mapped):
        assert OCITemplateMapper.validate_required_fields(template) == []
        payload = OCITemplateMapper.build_launch_payload(template, "catalog-test")
        shape = next(iter(example["machine_types"]))
        assert payload["shape"] == shape
        assert payload["source_details"]["image_id"] == source["image_id"]
        assert payload["create_vnic_details"] == {"subnet_id": "subnet-1", "nsg_ids": ["nsg-1"]}
        assert payload["metadata"]["user_data"] == source["user_data"]
        handler = OCIComputeHandler(MagicMock(), "us-phoenix-1")
        handler._run_oci = MagicMock(return_value={"data": {"id": "instance-test"}})
        handler._launch_with_cli(payload)
        args = handler._run_oci.call_args.args[0]
        assert args[args.index("--image-id") + 1] == source["image_id"]
        if shape.startswith("VM."):
            assert payload["shape_config"] == example["shape_config"]
            assert json.loads(args[args.index("--shape-config") + 1]) == example["shape_config"]
        else:
            assert "shape_config" not in payload
            assert "--shape-config" not in args
        normalized = OCITemplateMapper.normalize_template_fields(template)
        assert normalized["cpu_vendor"] == example["cpu_vendor"]
        assert normalized["generation"] == example["generation"]
        if example["cpu_vendor"] == "Ampere":
            assert "replace-arm64-image-ocid" in payload["source_details"]["image_id"]
        if "compute_cluster_id" in example:
            assert args[args.index("--compute-cluster-id") + 1] == example["compute_cluster_id"]


@pytest.mark.parametrize("shape", ["VM.DenseIO.E5.Flex", "VM.DenseIO.E6.Ax.Flex"])
@pytest.mark.parametrize("ocpus", [8, 16, 24, 32, 40, 48])
def test_dense_flex_accepts_documented_steps(shape, ocpus):
    template = {
        "template_id": "dense",
        "image_id": "image",
        "subnet_id": "subnet",
        "compartment_id": "compartment",
        "shape": shape,
        "shape_config": {"ocpus": ocpus, "memoryInGBs": ocpus * 12},
    }
    assert OCITemplateMapper.validate_required_fields(template) == []


@pytest.mark.parametrize("shape", ["VM.DenseIO.E5.Flex", "VM.DenseIO.E6.Ax.Flex"])
@pytest.mark.parametrize(
    "sizing",
    [
        {},
        {"ocpus": 1, "memoryInGBs": 16},
        {"ocpus": 8, "memoryInGBs": 128},
        {"ocpus": 9, "memoryInGBs": 108},
        {"ocpus": 56, "memoryInGBs": 672},
        {"ocpus": 8},
        {"memoryInGBs": 96},
    ],
)
def test_dense_flex_rejects_invalid_or_missing_steps(shape, sizing):
    errors = OCITemplateMapper.validate_required_fields({"shape": shape, "shape_config": sizing})
    assert "dense_flex_requires_supported_ocpu_memory_pair" in errors


@pytest.mark.parametrize("field", ["ocpus", "memoryInGBs"])
@pytest.mark.parametrize("value", [0, -1, "bad", float("inf"), float("nan")])
def test_flex_rejects_invalid_numeric_values(field, value):
    errors = OCITemplateMapper.validate_required_fields(
        {"shape": "VM.Standard.E5.Flex", "shape_config": {field: value}}
    )
    assert f"shape_config.{field}(must be finite and positive)" in errors


def test_flex_top_level_zero_does_not_fall_back_to_nested_sizing():
    errors = OCITemplateMapper.validate_required_fields(
        {"shape": "VM.Standard.E6.Flex", "ocpus": 0, "shape_config": {"ocpus": 2}}
    )
    assert "shape_config.ocpus(must be finite and positive)" in errors


def test_flex_sizing_aliases_become_canonical_cli_fields():
    normalized = OCITemplateMapper.normalize_template_fields(
        {"shape": "VM.Standard.A1.Flex", "shape_config": {"OCPUs": 2, "memory_gbs": 16}}
    )
    assert normalized["shape_config"] == {"ocpus": 2, "memoryInGBs": 16}


@pytest.mark.asyncio
async def test_invalid_dense_sizing_stops_before_live_cli():
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1")
    handler._oci_cli_available = True
    handler._run_oci = MagicMock(side_effect=AssertionError("Invalid sizing reached OCI"))
    result = await handler.create_instances(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_id": "invalid-dense",
                "image_id": "image",
                "shape": "VM.DenseIO.E5.Flex",
                "subnet_ids": ["subnet"],
                "compartment_id": "compartment",
                "shape_config": {"ocpus": 1, "memoryInGBs": 16},
                "count": 1,
            },
        )
    )
    assert not result.success
    assert "dense_flex_requires_supported_ocpu_memory_pair" in result.metadata["missing_fields"]
    handler._run_oci.assert_not_called()


def test_flex_size_can_change_without_changing_shape_or_architecture():
    example = next(t for t in CATALOG if t["template_id"] == "oci-vm-standard-a1-ondemand")
    payload = OCITemplateMapper.build_launch_payload(
        {**example, "shape_config": {"ocpus": 4, "memoryInGBs": 32}}, "resized"
    )
    assert payload["shape"] == "VM.Standard.A1.Flex"
    assert payload["shape_config"] == {"ocpus": 4, "memoryInGBs": 32}
    assert "replace-arm64-image-ocid" in payload["source_details"]["image_id"]

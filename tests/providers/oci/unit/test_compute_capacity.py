"""OCI hardware facts follow the selected shape and resolved launch sizing."""

import copy
import json
from dataclasses import FrozenInstanceError
from importlib.resources import files
from types import MappingProxyType

import pytest

from orb.providers.oci.mapping.template_mapper import OCITemplateMapper
from orb.providers.oci.services.compute_capacity import resolve_compute_capacity

CATALOG = json.loads(
    files("orb.providers.oci.config").joinpath("oci_templates.json").read_text(encoding="utf-8")
)["templates"]


def flex_template(shape="VM.Standard.E5.Flex", ocpus=4, memory=32):
    return {"shape": shape, "shape_config": {"ocpus": ocpus, "memoryInGBs": memory}}


@pytest.mark.parametrize("example", CATALOG, ids=lambda item: item["template_id"])
def test_every_catalog_template_has_known_capacity_matching_launch(example):
    template = {
        "image_id": "test-image",
        "compartment_id": "test-compartment",
        "subnet_ids": ["test-subnet"],
        **example,
    }
    original = copy.deepcopy(template)
    capacity = resolve_compute_capacity(template)
    assert capacity is not None
    payload = OCITemplateMapper.build_launch_payload(template, "capacity-test")
    assert capacity.shape == payload["shape"]
    assert capacity.memory_mib == capacity.memory_gbs * 1024
    if capacity.shape.startswith("VM."):
        assert capacity.ocpus == payload["shape_config"]["ocpus"]
        assert capacity.memory_gbs == payload["shape_config"]["memoryInGBs"]
    else:
        assert "shape_config" not in payload
    assert template == original


@pytest.mark.parametrize(
    ("shape", "architecture", "cores", "threads"),
    [
        ("VM.Standard.E5.Flex", "x86_64", 4, 8),
        ("VM.Standard.E6.Flex", "x86_64", 4, 8),
        ("VM.Standard.E6.Ax.Flex", "x86_64", 4, 8),
        ("VM.Standard3.Flex", "x86_64", 4, 8),
        ("VM.Standard4.Ax.Flex", "x86_64", 4, 8),
        ("VM.Optimized3.Flex", "x86_64", 4, 8),
        ("VM.Standard.A1.Flex", "aarch64", 4, 4),
        ("VM.Standard.A2.Flex", "aarch64", 8, 8),
        ("VM.Standard.A4.Flex", "aarch64", 8, 8),
        ("VM.Standard.A4.Ax.Flex", "aarch64", 8, 8),
    ],
)
def test_flex_cpu_topology_is_family_specific(shape, architecture, cores, threads):
    template = flex_template(shape)
    template["cpu_vendor"] = "misleading-label"
    capacity = resolve_compute_capacity(template)
    assert capacity is not None
    assert (capacity.architecture, capacity.physical_cores, capacity.logical_cpus) == (
        architecture,
        cores,
        threads,
    )
    assert capacity.ocpus == 4
    assert capacity.memory_mib == 32768


@pytest.mark.parametrize(
    ("shape", "ocpus", "memory", "cores", "threads"),
    [
        ("BM.Standard.E5.192", 192, 2304, 192, 384),
        ("BM.Standard.E6.256", 256, 3072, 256, 512),
        ("BM.Standard.E6.Ax.192", 192, 1536, 192, 384),
        ("BM.Standard3.64", 64, 1024, 64, 128),
        ("BM.Standard4.Ax.120", 120, 1152, 120, 240),
        ("BM.Standard.A1.160", 160, 1024, 160, 160),
        ("BM.Standard.A4.48", 48, 768, 96, 96),
        ("BM.Standard.A4.Ax.48", 48, 768, 96, 96),
        ("BM.DenseIO.E5.128", 128, 1536, 128, 256),
        ("BM.DenseIO.E6.Ax.192", 192, 2304, 192, 384),
        ("BM.Optimized3.36", 36, 512, 36, 72),
    ],
)
def test_fixed_shape_facts_match_oracle_reference(shape, ocpus, memory, cores, threads):
    capacity = resolve_compute_capacity({"shape": shape})
    assert capacity is not None
    assert (capacity.ocpus, capacity.memory_gbs) == (ocpus, memory)
    assert (capacity.physical_cores, capacity.logical_cpus) == (cores, threads)


def test_changing_flex_size_recomputes_without_mutating_prior_result():
    template = flex_template()
    first = resolve_compute_capacity(template)
    template["shape_config"] = {"ocpus": 8, "memoryInGBs": 64}
    second = resolve_compute_capacity(template)
    assert first is not None and second is not None
    assert (first.ocpus, first.memory_gbs) == (4, 32)
    assert (second.ocpus, second.memory_gbs) == (8, 64)
    with pytest.raises(FrozenInstanceError):
        first.ocpus = 9


@pytest.mark.parametrize("source", ["provider_config", "configuration", "metadata"])
def test_nested_launch_fields_are_resolved(source):
    template = {source: {"shape": "VM.Standard.A1.Flex", "ocpus": "4", "memoryInGBs": "32"}}
    capacity = resolve_compute_capacity(template)
    payload = OCITemplateMapper.build_launch_payload(template, "nested")
    assert capacity is not None
    assert capacity.architecture == "aarch64"
    assert capacity.ocpus == payload["shape_config"]["ocpus"] == 4
    assert capacity.memory_gbs == payload["shape_config"]["memoryInGBs"] == 32


def test_top_level_and_configuration_precedence_matches_launch_normalizer():
    template = {
        "shape": "VM.Standard.E5.Flex",
        "ocpus": 6,
        "provider_config": {"shape": "VM.Standard.A1.Flex", "ocpus": 3, "memory_gbs": 24},
        "configuration": {"ocpus": 4, "memory_gbs": 48},
        "metadata": {"ocpus": 1, "memory_gbs": 8},
        "shape_config": {"ocpus": 2, "memoryInGBs": 16},
    }
    capacity = resolve_compute_capacity(template)
    payload = OCITemplateMapper.build_launch_payload(template, "precedence")
    assert capacity is not None
    assert (capacity.ocpus, capacity.memory_gbs) == (6, 48)
    assert payload["shape_config"] == {"ocpus": 6, "memoryInGBs": 48}


@pytest.mark.parametrize(
    "sizing",
    [
        {"shapeConfig": {"OCPUs": 4, "memory_gbs": 32}},
        {"ocpu": 4, "memory_gb": 32},
        {"shape_ocpus": 4, "memory": 32},
    ],
)
def test_sizing_aliases_match_launch_fields(sizing):
    template = {"machine_types": {"VM.Standard.E5.Flex": 1}, **sizing}
    capacity = resolve_compute_capacity(MappingProxyType(template))
    assert capacity is not None
    assert (capacity.ocpus, capacity.memory_gbs) == (4, 32)
    assert OCITemplateMapper.build_launch_payload(template, "alias")["shape_config"] == {
        "ocpus": 4,
        "memoryInGBs": 32,
    }


@pytest.mark.parametrize("shape", ["VM.DenseIO.E5.Flex", "VM.DenseIO.E6.Ax.Flex"])
@pytest.mark.parametrize("ocpus", [8, 16, 24, 32, 40, 48])
def test_dense_flex_steps_keep_capacity_and_payload_aligned(shape, ocpus):
    capacity = resolve_compute_capacity(flex_template(shape, ocpus, ocpus * 12))
    assert capacity is not None
    assert (capacity.ocpus, capacity.memory_gbs) == (ocpus, ocpus * 12)


def test_unsupported_dense_pair_is_not_advertised():
    with pytest.raises(ValueError, match="supported OCPU/memory pair"):
        resolve_compute_capacity(flex_template("VM.DenseIO.E5.Flex", 8, 128))


@pytest.mark.parametrize(
    "template",
    [
        {},
        {"shape": "VM.Standard.E5.Flex"},
        {"shape": "VM.Standard.E5.Flex", "ocpus": 4},
        {"shape": "VM.Standard.E5.Flex", "memory_gbs": 32},
        {"shape": "VM.Unknown.E5.Flex", "ocpus": 4, "memory_gbs": 32},
        {"shape": "BM.Unknown.36"},
        {"machine_types": {"VM.Standard.E5.Flex": 1, "VM.Standard.A1.Flex": 1}},
    ],
)
def test_incomplete_or_unknown_capacity_does_not_guess(template):
    assert resolve_compute_capacity(template) is None


@pytest.mark.parametrize("field", ["ocpus", "memoryInGBs"])
@pytest.mark.parametrize("value", [True, False, 0, -1, "invalid", float("inf"), float("nan"), []])
def test_invalid_flex_values_raise_before_reporting(field, value):
    template = flex_template()
    template["shape_config"][field] = value
    with pytest.raises(ValueError, match="finite positive"):
        resolve_compute_capacity(template)


@pytest.mark.parametrize("value", [True, "bad", [], 0])
def test_malformed_shape_config_is_not_discarded(value):
    with pytest.raises(ValueError, match="shape_config must be an object"):
        resolve_compute_capacity({"shape": "VM.Standard.E5.Flex", "shape_config": value})


def test_invalid_top_level_value_does_not_fall_back_to_good_shape_config():
    template = flex_template()
    template["ocpus"] = False
    with pytest.raises(ValueError, match="not a boolean"):
        resolve_compute_capacity(template)


def test_lower_priority_malformed_fields_do_not_override_selected_valid_fields():
    template = flex_template()
    template["provider_config"] = {"shape_config": "ignored", "ocpus": None}
    template["metadata"] = {"shape_config": [], "ocpus": "invalid"}
    # Scalar OCI fields precede shape_config, even when stored in metadata.
    with pytest.raises(ValueError, match="ocpus"):
        resolve_compute_capacity(template)
    template["ocpus"] = 4
    assert resolve_compute_capacity(template) is not None


@pytest.mark.parametrize(
    "sizing", [{"ocpus": 36}, {"memory_gbs": 512}, {"shape_config": {"ocpus": 36}}]
)
def test_bare_metal_rejects_flex_overrides(sizing):
    with pytest.raises(ValueError, match="Bare metal"):
        resolve_compute_capacity({"shape": "BM.Optimized3.36", **sizing})


@pytest.mark.parametrize("ocpus,memory", [(1e308, 32), (4, 1e308), (4, 0.00001)])
def test_reporting_conversion_rejects_overflow_or_sub_mib_memory(ocpus, memory):
    with pytest.raises(ValueError, match="reporting units"):
        resolve_compute_capacity(flex_template(ocpus=ocpus, memory=memory))


def test_memory_reporting_rounds_down_to_a_whole_mib():
    capacity = resolve_compute_capacity(flex_template(memory=32.0005))
    assert capacity is not None
    assert capacity.memory_gbs == 32.0005
    assert capacity.memory_mib == 32768

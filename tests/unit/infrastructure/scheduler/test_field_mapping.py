"""Layer 1: Pure unit tests for field mapper input/output directions and transformations.

No file I/O, no DI container, no AWS.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from infrastructure.scheduler.default.field_mapper import DefaultFieldMapper
from infrastructure.scheduler.hostfactory.field_mapper import HostFactoryFieldMapper
from infrastructure.scheduler.hostfactory.field_mappings import HostFactoryFieldMappings
from infrastructure.scheduler.hostfactory.transformations import HostFactoryTransformations

# ---------------------------------------------------------------------------
# HF mapper — input direction (camelCase → snake_case)
# ---------------------------------------------------------------------------


def test_hf_map_input_produces_no_camelcase_keys_for_mapped_fields():
    """map_input_fields output must not contain camelCase HF keys for any mapped field.

    Keys that are already snake_case in the mapping table (e.g. 'iops') are
    identity-mapped and will appear in the output — that is correct behaviour.
    We only assert that keys with uppercase letters (true camelCase) do not leak.
    """
    mapper = HostFactoryFieldMapper("aws")
    all_hf_fields = set(HostFactoryFieldMappings.get_mappings("aws").keys())
    # Only the fields that are genuinely camelCase (contain an uppercase letter)
    camel_hf_fields = {f for f in all_hf_fields if any(c.isupper() for c in f)}
    sample = {f: f"val_{f}" for f in all_hf_fields}

    result = mapper.map_input_fields(sample)

    for hf_key in camel_hf_fields:
        assert hf_key not in result, f"camelCase key '{hf_key}' leaked into map_input_fields output"


def test_hf_map_input_copies_unmapped_fields():
    """map_input_fields preserves fields that have no mapping entry."""
    mapper = HostFactoryFieldMapper("aws")
    data = {"templateId": "t1", "customField": "keep_me", "anotherExtra": 42}

    result = mapper.map_input_fields(data)

    assert result.get("template_id") == "t1"
    assert result.get("customField") == "keep_me"
    assert result.get("anotherExtra") == 42


def test_hf_map_input_template_id():
    mapper = HostFactoryFieldMapper("aws")
    result = mapper.map_input_fields({"templateId": "my-tpl"})
    assert result["template_id"] == "my-tpl"


def test_hf_map_input_max_number():
    mapper = HostFactoryFieldMapper("aws")
    result = mapper.map_input_fields({"maxNumber": 10})
    assert result["max_instances"] == 10


def test_hf_map_input_subnet_id_string_becomes_list():
    """subnetId comma-delimited string → subnet_ids list via transformation."""
    mapper = HostFactoryFieldMapper("aws")
    result = mapper.map_input_fields({"subnetId": "subnet-aaa,subnet-bbb"})
    assert result["subnet_ids"] == ["subnet-aaa", "subnet-bbb"]


def test_hf_map_input_instance_tags_string_becomes_dict():
    """instanceTags semicolon-delimited string → tags dict via transformation."""
    mapper = HostFactoryFieldMapper("aws")
    result = mapper.map_input_fields({"instanceTags": "k1=v1;k2=v2"})
    assert result["tags"] == {"k1": "v1", "k2": "v2"}


def test_hf_map_input_instance_tags_dict_passthrough():
    """instanceTags already a dict passes through unchanged."""
    mapper = HostFactoryFieldMapper("aws")
    result = mapper.map_input_fields({"instanceTags": {"env": "prod"}})
    assert result["tags"] == {"env": "prod"}


# ---------------------------------------------------------------------------
# HF mapper — output direction (snake_case → camelCase)
# ---------------------------------------------------------------------------


def test_hf_map_output_template_id():
    mapper = HostFactoryFieldMapper("aws")
    result = mapper.map_output_fields({"template_id": "my-tpl"}, copy_unmapped=False)
    assert result.get("templateId") == "my-tpl"
    assert "template_id" not in result


def test_hf_map_output_max_instances():
    mapper = HostFactoryFieldMapper("aws")
    result = mapper.map_output_fields({"max_instances": 7}, copy_unmapped=False)
    assert result.get("maxNumber") == 7
    assert "max_instances" not in result


def test_hf_map_output_single_machine_type_becomes_vm_types():
    """machine_types with one entry → vmTypes dict (mapper always uses vmTypes for output)."""
    mapper = HostFactoryFieldMapper("aws")
    result = mapper.map_output_fields({"machine_types": {"t3.medium": 1}}, copy_unmapped=False)
    assert "vmTypes" in result
    assert result["vmTypes"] == {"t3.medium": 1}
    assert "machine_types" not in result


def test_hf_map_output_multiple_machine_types_becomes_vm_types():
    """machine_types with multiple entries → vmTypes dict."""
    mapper = HostFactoryFieldMapper("aws")
    result = mapper.map_output_fields(
        {"machine_types": {"t3.medium": 2, "t3.small": 1}}, copy_unmapped=False
    )
    assert "vmTypes" in result
    assert result["vmTypes"] == {"t3.medium": 2, "t3.small": 1}
    assert "machine_types" not in result


def test_hf_map_output_single_machine_type_non_unit_weight_becomes_vm_types():
    """machine_types with one entry but weight != 1 → vmTypes."""
    mapper = HostFactoryFieldMapper("aws")
    result = mapper.map_output_fields({"machine_types": {"t3.large": 3}}, copy_unmapped=False)
    assert "vmTypes" in result
    assert "machine_types" not in result


def test_hf_map_output_subnet_ids_list_becomes_comma_joined():
    """subnet_ids list → subnetId comma-joined string."""
    mapper = HostFactoryFieldMapper("aws")
    result = mapper.map_output_fields({"subnet_ids": ["subnet-a", "subnet-b"]}, copy_unmapped=False)
    assert result.get("subnetId") == "subnet-a,subnet-b"
    assert "subnet_ids" not in result
    assert "subnetIds" not in result


def test_hf_map_output_vm_type_produces_attributes():
    """attributes dict is generated by format_templates_response when vmType/vmTypes is present.

    The mapper itself only produces vmTypes; attributes are injected by
    HostFactorySchedulerStrategy.format_templates_response via _build_hf_attributes.
    We test that path here via the strategy, not the mapper directly.
    """
    from infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )
    from infrastructure.template.dtos import TemplateDTO

    strategy = HostFactorySchedulerStrategy()
    dto = TemplateDTO(template_id="t1", max_instances=1, machine_types={"t3.medium": 1})
    result = strategy.format_templates_response([dto])
    item = result["templates"][0]
    assert "attributes" in item
    attrs = item["attributes"]
    for key in ("type", "ncpus", "ncores", "nram"):
        assert key in attrs, f"attributes missing key '{key}'"
        assert isinstance(attrs[key], list)
        assert len(attrs[key]) == 2


# ---------------------------------------------------------------------------
# HF transformations (unit)
# ---------------------------------------------------------------------------


def test_transform_subnet_id_string_single():
    assert HostFactoryTransformations.transform_aws_subnet_id("subnet-abc") == ["subnet-abc"]


def test_transform_subnet_id_string_comma_delimited():
    result = HostFactoryTransformations.transform_aws_subnet_id("subnet-a, subnet-b")
    assert result == ["subnet-a", "subnet-b"]


def test_transform_subnet_id_list_passthrough():
    lst = ["subnet-x", "subnet-y"]
    assert HostFactoryTransformations.transform_aws_subnet_id(lst) == lst


def test_transform_subnet_id_other_type_returns_empty():
    assert HostFactoryTransformations.transform_aws_subnet_id(None) == []
    assert HostFactoryTransformations.transform_aws_subnet_id(42) == []


def test_transform_instance_tags_string():
    result = HostFactoryTransformations.transform_instance_tags("env=prod;team=infra")
    assert result == {"env": "prod", "team": "infra"}


def test_transform_instance_tags_empty_string():
    assert HostFactoryTransformations.transform_instance_tags("") == {}


def test_transform_instance_tags_dict_passthrough():
    d = {"env": "prod"}
    assert HostFactoryTransformations.transform_instance_tags(d) == d


def test_transform_instance_tags_other_type_returns_empty():
    assert HostFactoryTransformations.transform_instance_tags(None) == {}


def test_transform_user_data_plain_string_passthrough():
    """Non-path string with no path-like characters is returned as-is."""
    # Must not contain '/', '.sh', '.ps1', '.bat' — those trigger file-path detection
    plain = "base64encodedcloudinitdata=="
    result = HostFactoryTransformations.transform_user_data(plain)
    assert result == plain


def test_transform_user_data_missing_file_returns_original(tmp_path):
    """A path that doesn't exist returns the original value."""
    missing = str(tmp_path / "nonexistent.sh")
    result = HostFactoryTransformations.transform_user_data(missing)
    assert result == missing


def test_transform_user_data_reads_file_content(tmp_path):
    """A valid file path is read and its content returned."""
    script = tmp_path / "startup.sh"
    script.write_text("#!/bin/bash\necho from_file")
    result = HostFactoryTransformations.transform_user_data(str(script))
    assert result == "#!/bin/bash\necho from_file"


def test_apply_transformations_fires_subnet_and_tags():
    """apply_transformations processes subnet_ids and tags in one call."""
    data = {
        "subnet_ids": "subnet-a,subnet-b",
        "tags": "k=v",
    }
    result = HostFactoryTransformations.apply_transformations(data)
    assert result["subnet_ids"] == ["subnet-a", "subnet-b"]
    assert result["tags"] == {"k": "v"}


def test_apply_transformations_leaves_unrelated_fields_intact():
    data = {"template_id": "t1", "max_instances": 3}
    result = HostFactoryTransformations.apply_transformations(data)
    assert result["template_id"] == "t1"
    assert result["max_instances"] == 3


# ---------------------------------------------------------------------------
# Default mapper — identity contract
# ---------------------------------------------------------------------------


def test_default_map_input_is_identity():
    mapper = DefaultFieldMapper()
    data = {"template_id": "t1", "max_instances": 5, "machine_types": {"t3.micro": 1}}
    assert mapper.map_input_fields(data) == data


def test_default_map_output_is_identity():
    mapper = DefaultFieldMapper()
    data = {"template_id": "t1", "max_instances": 5}
    assert mapper.map_output_fields(data) == data


def test_default_format_for_generation_no_camelcase():
    """format_for_generation for Default must not introduce any camelCase keys."""
    mapper = DefaultFieldMapper()
    templates = [{"template_id": "t1", "max_instances": 2, "machine_types": {"t3.micro": 1}}]
    result = mapper.format_for_generation(templates)
    assert len(result) == 1
    for key in result[0]:
        # No camelCase: all keys should be lowercase or snake_case
        assert key == key.lower() or "_" in key, f"camelCase key '{key}' found in Default output"


def test_default_map_input_empty_dict():
    mapper = DefaultFieldMapper()
    assert mapper.map_input_fields({}) == {}


def test_default_map_output_empty_dict():
    mapper = DefaultFieldMapper()
    assert mapper.map_output_fields({}) == {}

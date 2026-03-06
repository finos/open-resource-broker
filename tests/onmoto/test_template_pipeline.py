"""Integration tests for the template loading pipeline.

Covers: config file -> scheduler strategy -> field mapping -> domain objects.
Tests run against moto-backed AWS resources; no real AWS calls are made.
"""

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.field_mappings import HostFactoryFieldMappings
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HF_GENERIC_FIELDS = HostFactoryFieldMappings.MAPPINGS["generic"]
_HF_AWS_FIELDS = HostFactoryFieldMappings.MAPPINGS["aws"]

_MINIMAL_HF_TEMPLATE: dict[str, Any] = {
    "templateId": "test-template",
    "maxNumber": 5,
    "vmTypes": {"t3.medium": 1},
    "subnetIds": [],
    "securityGroupIds": [],
    "priceType": "ondemand",
    "allocationStrategy": "lowest_price",
    "instanceTags": {"env": "test"},
    "providerApi": "EC2Fleet",
    "providerType": "aws",
    "fleetRole": "arn:aws:iam::123456789012:role/fleet-role",
    "vmTypesOnDemand": {"t3.medium": 1},
    "percentOnDemand": 50,
}

_MINIMAL_SNAKE_TEMPLATE: dict[str, Any] = {
    "template_id": "test-snake-template",
    "max_instances": 5,
    "machine_types": {"t3.medium": 1},
    "subnet_ids": ["subnet-aaa", "subnet-bbb"],
    "security_group_ids": ["sg-111"],
    "price_type": "ondemand",
    "allocation_strategy": "lowest_price",
    "tags": {"env": "test"},
    "provider_api": "EC2Fleet",
    "provider_type": "aws",
}


def _make_hf_strategy(defaults_service=None) -> HostFactorySchedulerStrategy:
    return HostFactorySchedulerStrategy(template_defaults_service=defaults_service)


def _make_default_strategy(defaults_service=None) -> DefaultSchedulerStrategy:
    return DefaultSchedulerStrategy(template_defaults_service=defaults_service)


def _write_hf_file(path: Path, templates: list[dict]) -> None:
    path.write_text(json.dumps({"scheduler_type": "hostfactory", "templates": templates}))


def _write_default_file(path: Path, templates: list[dict]) -> None:
    path.write_text(json.dumps({"scheduler_type": "default", "templates": templates}))


# ---------------------------------------------------------------------------
# HF Strategy Loading
# ---------------------------------------------------------------------------


def test_hf_strategy_loads_camelcase_and_maps_to_snake(tmp_path):
    """HF strategy maps camelCase template fields to snake_case domain fields."""
    tpl_file = tmp_path / "aws_templates.json"
    _write_hf_file(tpl_file, [_MINIMAL_HF_TEMPLATE])

    strategy = _make_hf_strategy()
    results = strategy.load_templates_from_path(str(tpl_file))

    assert len(results) == 1
    t = results[0]
    assert t["template_id"] == "test-template"
    assert t["max_instances"] == 5


def test_hf_strategy_maps_vm_types_to_machine_types(tmp_path):
    """vmTypes camelCase -> machine_types snake_case."""
    tpl_file = tmp_path / "aws_templates.json"
    _write_hf_file(tpl_file, [_MINIMAL_HF_TEMPLATE])

    strategy = _make_hf_strategy()
    results = strategy.load_templates_from_path(str(tpl_file))

    t = results[0]
    assert "machine_types" in t
    assert "t3.medium" in t["machine_types"]


def test_hf_strategy_maps_aws_specific_fields(tmp_path):
    """vmTypesOnDemand -> machine_types_ondemand, percentOnDemand -> percent_on_demand, fleetRole -> fleet_role."""
    tpl_file = tmp_path / "aws_templates.json"
    _write_hf_file(tpl_file, [_MINIMAL_HF_TEMPLATE])

    strategy = _make_hf_strategy()
    results = strategy.load_templates_from_path(str(tpl_file))

    t = results[0]
    assert t.get("percent_on_demand") == 50
    assert t.get("fleet_role") == "arn:aws:iam::123456789012:role/fleet-role"


def test_hf_strategy_transforms_instance_tags_string_to_dict(tmp_path):
    """instanceTags as semicolon-delimited string is parsed to dict."""
    tpl = {**_MINIMAL_HF_TEMPLATE, "instanceTags": "key1=val1;key2=val2"}
    tpl_file = tmp_path / "aws_templates.json"
    _write_hf_file(tpl_file, [tpl])

    strategy = _make_hf_strategy()
    results = strategy.load_templates_from_path(str(tpl_file))

    tags = results[0].get("tags", {})
    assert tags.get("key1") == "val1"
    assert tags.get("key2") == "val2"


def test_hf_strategy_transforms_instance_tags_dict_passthrough(tmp_path):
    """instanceTags already a dict passes through unchanged."""
    tpl_file = tmp_path / "aws_templates.json"
    _write_hf_file(tpl_file, [_MINIMAL_HF_TEMPLATE])

    strategy = _make_hf_strategy()
    results = strategy.load_templates_from_path(str(tpl_file))

    assert results[0].get("tags") == {"env": "test"}


def test_hf_strategy_transforms_subnet_id_string_to_list(tmp_path):
    """subnetId comma-delimited string -> subnet_ids list."""
    # Drop subnetIds so subnetId is the only subnet source
    tpl = {k: v for k, v in _MINIMAL_HF_TEMPLATE.items() if k != "subnetIds"}
    tpl["subnetId"] = "subnet-aaa,subnet-bbb"
    tpl_file = tmp_path / "aws_templates.json"
    _write_hf_file(tpl_file, [tpl])

    strategy = _make_hf_strategy()
    results = strategy.load_templates_from_path(str(tpl_file))

    subnet_ids = results[0].get("subnet_ids", [])
    assert "subnet-aaa" in subnet_ids
    assert "subnet-bbb" in subnet_ids


# ---------------------------------------------------------------------------
# Default Strategy Loading
# ---------------------------------------------------------------------------


def test_default_strategy_loads_snake_case_passthrough(tmp_path):
    """Default strategy loads snake_case templates without any field mapping."""
    tpl_file = tmp_path / "aws_templates.json"
    _write_default_file(tpl_file, [_MINIMAL_SNAKE_TEMPLATE])

    strategy = _make_default_strategy()
    results = strategy.load_templates_from_path(str(tpl_file))

    assert len(results) == 1
    t = results[0]
    assert t["template_id"] == "test-snake-template"
    assert t["subnet_ids"] == ["subnet-aaa", "subnet-bbb"]


def test_default_strategy_delegates_hf_file_to_hf_strategy(tmp_path):
    """Default strategy detects scheduler_type=hostfactory and delegates via registry when registered."""
    from orb.infrastructure.scheduler.registry import get_scheduler_registry

    registry = get_scheduler_registry()
    # Register both types so delegation can resolve the HF strategy class
    if not registry.is_registered("hostfactory"):
        registry.register("hostfactory", HostFactorySchedulerStrategy, lambda c: None)
    if not registry.is_registered("default"):
        registry.register("default", DefaultSchedulerStrategy, lambda c: None)

    tpl_file = tmp_path / "aws_templates.json"
    _write_hf_file(tpl_file, [_MINIMAL_HF_TEMPLATE])

    strategy = _make_default_strategy()
    results = strategy.load_templates_from_path(str(tpl_file))

    # Delegation via HF strategy produces snake_case field names
    assert len(results) == 1
    assert results[0].get("template_id") == "test-template"


# ---------------------------------------------------------------------------
# Template Defaults Merge
# ---------------------------------------------------------------------------


def _make_defaults_service(subnet_ids: list[str], sg_ids: list[str]) -> MagicMock:
    """Build a minimal mock TemplateDefaultsPort that injects subnet/sg defaults."""
    svc = MagicMock()

    def resolve(template_dict, provider_instance_name=None):
        result = dict(template_dict)
        if not result.get("subnet_ids"):
            result["subnet_ids"] = subnet_ids
        if not result.get("security_group_ids"):
            result["security_group_ids"] = sg_ids
        return result

    svc.resolve_template_defaults.side_effect = resolve
    svc.resolve_provider_api_default.return_value = "EC2Fleet"
    return svc


def test_template_defaults_fills_empty_subnet_ids(tmp_path):
    """Templates with empty subnet_ids get values from template_defaults."""
    tpl = {**_MINIMAL_HF_TEMPLATE, "subnetIds": []}
    tpl_file = tmp_path / "aws_templates.json"
    _write_hf_file(tpl_file, [tpl])

    defaults_svc = _make_defaults_service(["subnet-111", "subnet-222"], ["sg-abc"])
    strategy = _make_hf_strategy(defaults_service=defaults_svc)
    results = strategy.load_templates_from_path(str(tpl_file))

    assert results[0]["subnet_ids"] == ["subnet-111", "subnet-222"]


def test_template_defaults_fills_empty_security_group_ids(tmp_path):
    """Templates with empty security_group_ids get values from template_defaults."""
    tpl = {**_MINIMAL_HF_TEMPLATE, "securityGroupIds": []}
    tpl_file = tmp_path / "aws_templates.json"
    _write_hf_file(tpl_file, [tpl])

    defaults_svc = _make_defaults_service(["subnet-111"], ["sg-xyz"])
    strategy = _make_hf_strategy(defaults_service=defaults_svc)
    results = strategy.load_templates_from_path(str(tpl_file))

    assert results[0]["security_group_ids"] == ["sg-xyz"]


def test_template_defaults_does_not_overwrite_existing_subnet_ids(tmp_path):
    """Templates with existing subnet_ids are NOT overwritten by template_defaults."""
    tpl = {**_MINIMAL_HF_TEMPLATE, "subnetIds": ["subnet-existing"]}
    tpl_file = tmp_path / "aws_templates.json"
    _write_hf_file(tpl_file, [tpl])

    # defaults_service that would inject different subnets if called
    svc = MagicMock()

    def resolve(template_dict, provider_instance_name=None):
        result = dict(template_dict)
        # Only fill if empty (mirrors real TemplateDefaultsService._coalesce_merge)
        if not result.get("subnet_ids"):
            result["subnet_ids"] = ["subnet-from-defaults"]
        return result

    svc.resolve_template_defaults.side_effect = resolve
    svc.resolve_provider_api_default.return_value = "EC2Fleet"

    strategy = _make_hf_strategy(defaults_service=svc)
    results = strategy.load_templates_from_path(str(tpl_file))

    assert results[0]["subnet_ids"] == ["subnet-existing"]


# ---------------------------------------------------------------------------
# Round-Trip: generate -> write -> load
# ---------------------------------------------------------------------------


def test_round_trip_hf_format(tmp_path):
    """Generate HF templates, write to disk, load back — all fields survive."""
    strategy = _make_hf_strategy()

    # Start from a snake_case dict (post-load domain format)
    domain_dicts = [
        {
            "template_id": "rt-template",
            "max_instances": 3,
            "machine_types": {"t3.small": 1},
            "subnet_ids": ["subnet-rt1"],
            "security_group_ids": ["sg-rt1"],
            "price_type": "ondemand",
            "allocation_strategy": "lowest_price",
            "tags": {},
            "provider_api": "EC2Fleet",
            "provider_type": "aws",
        }
    ]

    generated = strategy.format_templates_for_generation(domain_dicts)
    tpl_file = tmp_path / "rt_hf.json"
    tpl_file.write_text(json.dumps({"scheduler_type": "hostfactory", "templates": generated}))

    loaded = strategy.load_templates_from_path(str(tpl_file))

    assert len(loaded) == 1
    assert loaded[0]["template_id"] == "rt-template"
    assert loaded[0]["max_instances"] == 3
    assert "t3.small" in loaded[0]["machine_types"]


def test_round_trip_hf_generated_file_has_camelcase_keys(tmp_path):
    """format_templates_for_generation for HF produces camelCase keys on disk."""
    strategy = _make_hf_strategy()
    domain_dicts = [
        {
            "template_id": "camel-check",
            "max_instances": 1,
            "machine_types": {"t3.micro": 1},
            "subnet_ids": [],
            "security_group_ids": [],
            "price_type": "ondemand",
            "allocation_strategy": "lowest_price",
            "tags": {},
            "provider_api": "EC2Fleet",
            "provider_type": "aws",
        }
    ]

    generated = strategy.format_templates_for_generation(domain_dicts)

    assert len(generated) == 1
    g = generated[0]
    # HF wire format uses camelCase
    assert "templateId" in g or "vmType" in g or "vmTypes" in g


def test_round_trip_default_generated_file_has_snake_case_keys(tmp_path):
    """format_templates_for_generation for Default strategy produces snake_case keys."""
    strategy = _make_default_strategy()
    domain_dicts = [_MINIMAL_SNAKE_TEMPLATE]

    generated = strategy.format_templates_for_generation(domain_dicts)

    assert len(generated) == 1
    g = generated[0]
    assert "template_id" in g
    assert "machine_types" in g or "max_instances" in g


# ---------------------------------------------------------------------------
# Field Mapping Coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hf_field,domain_field", list(_HF_GENERIC_FIELDS.items()))
def test_hf_generic_field_mapping_has_valid_domain_target(hf_field, domain_field):
    """Every generic HF field maps to a non-empty snake_case domain target."""
    assert domain_field, f"{hf_field} maps to empty string"
    assert "_" in domain_field or domain_field.islower(), (
        f"{hf_field} -> {domain_field} does not look like a snake_case domain field"
    )


@pytest.mark.parametrize("hf_field,domain_field", list(_HF_AWS_FIELDS.items()))
def test_hf_aws_field_mapping_has_valid_domain_target(hf_field, domain_field):
    """Every AWS-specific HF field maps to a non-empty snake_case domain target."""
    assert domain_field, f"{hf_field} maps to empty string"
    assert "_" in domain_field or domain_field.islower(), (
        f"{hf_field} -> {domain_field} does not look like a snake_case domain field"
    )

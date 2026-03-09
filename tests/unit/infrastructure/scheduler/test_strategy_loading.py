"""Layer 2: Strategy loading, parsing, and generation tests.

Uses tmp_path to write fixture files. No DI container, no AWS.
"""

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.dtos import TemplateDTO
from tests.unit.infrastructure.scheduler.conftest import (
    _MINIMAL_HF_TEMPLATE_ON_DISK,
    _MINIMAL_SNAKE_TEMPLATE,
    SCHEDULER_CONFIGS,
    make_default_strategy,
    make_hf_strategy,
    write_default_file,
    write_hf_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HF_TEMPLATE_WITH_TAGS: dict[str, Any] = {
    **_MINIMAL_HF_TEMPLATE_ON_DISK,
    "instanceTags": "env=test;team=infra",
}


# ---------------------------------------------------------------------------
# load_templates_from_path — both schedulers
# ---------------------------------------------------------------------------


def test_load_returns_empty_for_nonexistent_path():
    for cfg in SCHEDULER_CONFIGS.values():
        strategy = cfg["strategy_factory"]()
        result = strategy.load_templates_from_path("/nonexistent/path/templates.json")
        assert result == []


def test_load_returns_empty_for_empty_templates_list(tmp_path):
    for scheduler_type, cfg in SCHEDULER_CONFIGS.items():
        f = tmp_path / f"{scheduler_type}_empty.json"
        cfg["write_file_fn"](f, [])
        strategy = cfg["strategy_factory"]()
        result = strategy.load_templates_from_path(str(f))
        assert result == [], f"{scheduler_type}: expected [] for empty templates list"


def test_load_output_always_has_snake_case_domain_key(tmp_path):
    """Both strategies must produce snake_case domain keys after loading."""
    for scheduler_type, cfg in SCHEDULER_CONFIGS.items():
        f = tmp_path / f"{scheduler_type}_tpl.json"
        cfg["write_file_fn"](f, [cfg["minimal_template_on_disk"]])
        strategy = cfg["strategy_factory"]()
        results = strategy.load_templates_from_path(str(f))
        assert len(results) == 1, f"{scheduler_type}: expected 1 template"
        assert cfg["expected_domain_key"] in results[0], (
            f"{scheduler_type}: domain key '{cfg['expected_domain_key']}' missing from loaded template"
        )


# ---------------------------------------------------------------------------
# HF strategy — load_templates_from_path specifics
# ---------------------------------------------------------------------------


def test_hf_load_skips_none_templates(tmp_path):
    """HF strategy skips None entries without raising."""
    f = tmp_path / "hf_with_none.json"
    # Write raw JSON with a null entry
    f.write_text(
        json.dumps(
            {
                "scheduler_type": "hostfactory",
                "templates": [None, _MINIMAL_HF_TEMPLATE_ON_DISK],
            }
        )
    )
    strategy = make_hf_strategy()
    results = strategy.load_templates_from_path(str(f))
    # The valid template should still be loaded
    assert len(results) == 1
    assert results[0]["template_id"] == _MINIMAL_HF_TEMPLATE_ON_DISK["templateId"]


def test_hf_load_skips_malformed_template_continues_with_valid(tmp_path):
    """HF strategy skips a template missing required fields and loads the valid one."""
    malformed = {"maxNumber": 3}  # no templateId
    valid = _MINIMAL_HF_TEMPLATE_ON_DISK
    f = tmp_path / "hf_mixed.json"
    write_hf_file(f, [malformed, valid])
    strategy = make_hf_strategy()
    results = strategy.load_templates_from_path(str(f))
    # At least the valid template should be present
    template_ids = [r.get("template_id") for r in results]
    assert valid["templateId"] in template_ids


def test_hf_load_maps_camelcase_to_snake(tmp_path):
    f = tmp_path / "hf.json"
    write_hf_file(f, [_MINIMAL_HF_TEMPLATE_ON_DISK])
    strategy = make_hf_strategy()
    results = strategy.load_templates_from_path(str(f))
    t = results[0]
    assert t["template_id"] == "hf-tpl-001"
    assert t["max_instances"] == 5


def test_hf_load_transforms_instance_tags_string(tmp_path):
    f = tmp_path / "hf_tags.json"
    write_hf_file(f, [_HF_TEMPLATE_WITH_TAGS])
    strategy = make_hf_strategy()
    results = strategy.load_templates_from_path(str(f))
    assert results[0]["tags"] == {"env": "test", "team": "infra"}


# ---------------------------------------------------------------------------
# Default strategy — load_templates_from_path specifics
# ---------------------------------------------------------------------------


def test_default_load_passthrough_snake_case(tmp_path):
    f = tmp_path / "default.json"
    write_default_file(f, [_MINIMAL_SNAKE_TEMPLATE])
    strategy = make_default_strategy()
    results = strategy.load_templates_from_path(str(f))
    t = results[0]
    assert t["template_id"] == "default-tpl-001"
    assert t["subnet_ids"] == ["subnet-aaa"]


def test_default_load_delegates_hf_file_to_hf_strategy(tmp_path):
    """Default strategy detects scheduler_type=hostfactory and delegates to HF strategy."""
    from orb.infrastructure.scheduler.registry import get_scheduler_registry

    registry = get_scheduler_registry()
    if not registry.is_registered("hostfactory"):
        registry.register("hostfactory", HostFactorySchedulerStrategy, lambda c: None)
    if not registry.is_registered("default"):
        registry.register("default", DefaultSchedulerStrategy, lambda c: None)

    f = tmp_path / "hf_file.json"
    write_hf_file(f, [_MINIMAL_HF_TEMPLATE_ON_DISK])
    strategy = make_default_strategy()
    results = strategy.load_templates_from_path(str(f))
    assert len(results) == 1
    assert results[0]["template_id"] == _MINIMAL_HF_TEMPLATE_ON_DISK["templateId"]


def test_hf_load_delegates_default_file_to_default_strategy(tmp_path):
    """HF strategy detects scheduler_type=default and delegates to Default strategy."""
    from orb.infrastructure.scheduler.registry import get_scheduler_registry

    registry = get_scheduler_registry()
    if not registry.is_registered("hostfactory"):
        registry.register("hostfactory", HostFactorySchedulerStrategy, lambda c: None)
    if not registry.is_registered("default"):
        registry.register("default", DefaultSchedulerStrategy, lambda c: None)

    f = tmp_path / "default_file.json"
    write_default_file(f, [_MINIMAL_SNAKE_TEMPLATE])
    strategy = make_hf_strategy()
    results = strategy.load_templates_from_path(str(f))
    assert len(results) == 1
    assert results[0]["template_id"] == _MINIMAL_SNAKE_TEMPLATE["template_id"]


# ---------------------------------------------------------------------------
# parse_request_data — HF strategy
# ---------------------------------------------------------------------------


def test_hf_parse_request_data_nested_template():
    strategy = make_hf_strategy()
    raw = {"template": {"templateId": "t1", "machineCount": 3}}
    result = strategy.parse_request_data(raw)
    assert isinstance(result, dict)
    assert result["template_id"] == "t1"
    assert result["requested_count"] == 3


def test_hf_parse_request_data_flat_format():
    strategy = make_hf_strategy()
    raw = {"templateId": "t2", "maxNumber": 2}
    result = strategy.parse_request_data(raw)
    assert isinstance(result, dict)
    assert result["template_id"] == "t2"
    assert result["requested_count"] == 2


def test_hf_parse_request_data_requests_list():
    strategy = make_hf_strategy()
    raw = {"requests": [{"requestId": "req-abc"}, {"requestId": "req-def"}]}
    result = strategy.parse_request_data(raw)
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["request_id"] == "req-abc"
    assert result[1]["request_id"] == "req-def"


def test_hf_parse_request_data_single_request_dict():
    strategy = make_hf_strategy()
    raw = {"requests": {"requestId": "req-xyz"}}
    result = strategy.parse_request_data(raw)
    assert isinstance(result, list)
    assert result[0]["request_id"] == "req-xyz"


# ---------------------------------------------------------------------------
# parse_request_data — Default strategy
# ---------------------------------------------------------------------------


def test_default_parse_request_data_nested_template():
    strategy = make_default_strategy()
    raw = {"template": {"template_id": "t1", "machine_count": 3}}
    result = strategy.parse_request_data(raw)
    assert isinstance(result, dict)
    assert result["template_id"] == "t1"
    assert result["requested_count"] == 3


def test_default_parse_request_data_nested_template_camelcase_fallback():
    """Default strategy also accepts camelCase nested template keys."""
    strategy = make_default_strategy()
    raw = {"template": {"templateId": "t1", "machineCount": 2}}
    result = strategy.parse_request_data(raw)
    assert isinstance(result, dict)
    assert result["template_id"] == "t1"
    assert result["requested_count"] == 2


def test_default_parse_request_data_flat_snake_case():
    strategy = make_default_strategy()
    raw = {"template_id": "t3", "requested_count": 5}
    result = strategy.parse_request_data(raw)
    assert isinstance(result, dict)
    assert result["template_id"] == "t3"
    assert result["requested_count"] == 5


def test_default_parse_request_data_requests_list():
    strategy = make_default_strategy()
    raw = {"requests": [{"request_id": "req-111"}, {"request_id": "req-222"}]}
    result = strategy.parse_request_data(raw)
    assert isinstance(result, list)
    assert result[0]["request_id"] == "req-111"
    assert result[1]["request_id"] == "req-222"


# ---------------------------------------------------------------------------
# parse_template_config — HF strategy
# ---------------------------------------------------------------------------


def test_hf_parse_template_config_maps_camelcase_to_dto():
    strategy = make_hf_strategy()
    raw = {
        "templateId": "hf-cfg-001",
        "maxNumber": 4,
        "vmType": "t3.large",
        "priceType": "spot",
        "providerApi": "EC2Fleet",
    }
    dto = strategy.parse_template_config(raw)
    assert isinstance(dto, TemplateDTO)
    assert dto.template_id == "hf-cfg-001"
    assert dto.max_instances == 4
    assert dto.price_type == "spot"


def test_hf_parse_template_config_provider_api_alias_normalisation():
    """Alias normalisation delegates to the provider strategy via the registry.

    When no registry is wired the raw value passes through unchanged.
    When a registry is wired it delegates to the provider strategy's resolve_api_alias.
    """
    from unittest.mock import MagicMock

    # No registry — passthrough
    strategy = make_hf_strategy()
    for alias in ("AutoScalingGroup", "asg", "autoscalinggroup"):
        raw = {"templateId": "t1", "providerApi": alias}
        dto = strategy.parse_template_config(raw)
        assert dto.provider_api == alias, (
            f"without registry, alias '{alias}' should pass through unchanged"
        )

    # With registry wired — delegates to provider strategy
    mock_registry_service = MagicMock()
    mock_registry_service.select_active_provider.return_value = MagicMock(provider_name="aws")
    mock_registry_service.resolve_api_alias.side_effect = lambda provider_id, raw: (
        "ASG" if raw.lower() in ("autoscalinggroup", "asg") else raw
    )
    strategy_with_registry = make_hf_strategy()
    strategy_with_registry._provider_registry_service = mock_registry_service
    for alias in ("AutoScalingGroup", "asg", "autoscalinggroup"):
        raw = {"templateId": "t1", "providerApi": alias}
        dto = strategy_with_registry.parse_template_config(raw)
        assert dto.provider_api == "ASG", (
            f"with registry, alias '{alias}' should be normalised to 'ASG'"
        )


# ---------------------------------------------------------------------------
# parse_template_config — Default strategy
# ---------------------------------------------------------------------------


def test_default_parse_template_config_creates_template_object():
    from orb.domain.template.template_aggregate import Template

    strategy = make_default_strategy()
    raw = {
        "template_id": "default-cfg-001",
        "max_instances": 3,
        "machine_types": {"t3.micro": 1},
        "price_type": "ondemand",
    }
    template = strategy.parse_template_config(raw)
    assert isinstance(template, Template)
    assert template.template_id == "default-cfg-001"
    assert template.max_instances == 3


# ---------------------------------------------------------------------------
# format_templates_for_generation round-trip
# ---------------------------------------------------------------------------


def test_hf_generation_produces_camelcase_keys():
    """format_templates_for_generation for HF produces camelCase sentinel key."""
    strategy = make_hf_strategy()
    generated = strategy.format_templates_for_generation([_MINIMAL_SNAKE_TEMPLATE])
    assert len(generated) == 1
    g = generated[0]
    assert "templateId" in g, "HF generation must produce 'templateId' camelCase key"
    assert "template_id" not in g


def test_hf_generation_no_snake_case_for_mapped_fields():
    """No snake_case keys for fields that have a defined HF mapping."""
    from orb.infrastructure.scheduler.hostfactory.field_mappings import HostFactoryFieldMappings

    strategy = make_hf_strategy()
    generated = strategy.format_templates_for_generation([_MINIMAL_SNAKE_TEMPLATE])
    g = generated[0]
    all_domain_fields = set(HostFactoryFieldMappings.get_mappings("aws").values())
    for domain_field in all_domain_fields:
        assert domain_field not in g, (
            f"snake_case domain field '{domain_field}' leaked into HF generation output"
        )


def test_hf_round_trip_generate_write_load(tmp_path):
    """Generate → write → load produces the same template_id and max_instances."""
    strategy = make_hf_strategy()
    generated = strategy.format_templates_for_generation([_MINIMAL_SNAKE_TEMPLATE])
    f = tmp_path / "rt.json"
    write_hf_file(f, generated)
    loaded = strategy.load_templates_from_path(str(f))
    assert len(loaded) == 1
    assert loaded[0]["template_id"] == _MINIMAL_SNAKE_TEMPLATE["template_id"]
    assert loaded[0]["max_instances"] == _MINIMAL_SNAKE_TEMPLATE["max_instances"]


def test_default_generation_produces_snake_case_keys():
    """format_templates_for_generation for Default produces snake_case keys."""
    strategy = make_default_strategy()
    generated = strategy.format_templates_for_generation([_MINIMAL_SNAKE_TEMPLATE])
    assert len(generated) == 1
    g = generated[0]
    assert "template_id" in g
    # No camelCase keys should be introduced
    camel_keys = [k for k in g if k != k.lower() and "_" not in k and k[0].islower()]
    assert camel_keys == [], f"camelCase keys found in Default generation output: {camel_keys}"


def test_default_round_trip_generate_write_load(tmp_path):
    """Default: generate → write → load produces matching template."""
    strategy = make_default_strategy()
    generated = strategy.format_templates_for_generation([_MINIMAL_SNAKE_TEMPLATE])
    f = tmp_path / "rt_default.json"
    write_default_file(f, generated)
    loaded = strategy.load_templates_from_path(str(f))
    assert len(loaded) == 1
    assert loaded[0]["template_id"] == _MINIMAL_SNAKE_TEMPLATE["template_id"]
    assert loaded[0]["max_instances"] == _MINIMAL_SNAKE_TEMPLATE["max_instances"]

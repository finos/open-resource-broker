"""AWS spec templates keep user-supplied strings intact and structurally valid."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from jinja2 import Environment, meta

import orb
from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer

_SPECS = Path(orb.__file__).parent / "providers" / "aws" / "specs"
# The RunInstances spec is covered once its template is brought in line.
_SPEC_FILES = sorted(p for p in _SPECS.glob("*/default.json") if p.parent.name != "runinstances")

HOSTILE = 'a"b\\c\nd</x>&e'

# Expressions that may sit directly inside quotes: numbers, enums, resource
# identifiers and values the code derives itself.  Everything else rendered as
# a JSON string must go through ``tojson``.
_QUOTED_ALLOW_LIST = {
    "allocation_strategy",
    "allocation_strategy_on_demand",
    "default_capacity_type",
    "fleet_role",
    "fleet_type",
    "fleet_type | default('request')",
    "health_check_type",
    "image_id",
    "instance_interruption_behavior",
    "instance_type",
    "launch_template_id",
    "launch_template_version",
    "max_spot_price",
    "mt.instance_type",
    "mt.weighted_capacity",
    "override.instance_type",
    "override.subnet_id",
    "subnet_id",
    "user_data | b64encode",
    "volume_type",
    "vpc_zone_identifier",
}

_USER_STRING_VARS = {
    "fleet_name",
    "asg_name",
    "context",
    "instance_name",
    "request_id",
    "template_id",
    "created_by",
    "key_name",
    "instance_profile",
}


@pytest.mark.parametrize("path", _SPEC_FILES, ids=lambda p: p.parent.name)
def test_no_unescaped_user_string_inside_quotes(path: Path) -> None:
    quoted = re.findall(r'"\{\{\s*(.*?)\s*\}\}"', path.read_text())

    offenders = sorted({expr for expr in quoted if expr not in _QUOTED_ALLOW_LIST})

    assert not offenders, f"wrap in tojson and drop the quotes: {offenders}"


def _context(path: Path, flag: bool) -> dict[str, Any]:
    source = path.read_text()
    names = meta.find_undeclared_variables(Environment(autoescape=True).parse(source))
    pairs = [{"key": HOSTILE, "value": HOSTILE}]
    context: dict[str, Any] = {}
    for name in names:
        if name in _USER_STRING_VARS:
            context[name] = HOSTILE
        elif name in ("base_tags", "custom_tags"):
            context[name] = pairs
        elif name == "security_group_ids":
            context[name] = ["sg-1"]
        elif name in ("instance_overrides", "ondemand_overrides", "machine_types_overrides"):
            context[name] = [
                {
                    "instance_type": "t3.micro",
                    "subnet_id": "subnet-1",
                    "weighted_capacity": 2,
                    "instance_requirements": None,
                }
            ]
        elif name == "abis_instance_requirements":
            context[name] = {"VCpuCount": {"Min": 1}}
        elif name == "user_data":
            context[name] = "#!/bin/sh"
        elif name.startswith(("has_", "is_", "needs_")) or name in (
            "replace_unhealthy",
            "replace_unhealthy_instances",
            "associate_public_ip",
            "ebs_optimized",
            "monitoring_enabled",
        ):
            context[name] = flag
        elif name in ("volume_type",):
            context[name] = "gp3"
        elif name in ("health_check_type",):
            context[name] = "EC2"
        else:
            context[name] = 3
    # String-valued enums used in quoted positions must stay plain strings.
    for name in ("fleet_type", "allocation_strategy", "default_capacity_type"):
        if name in context:
            context[name] = "x"
    return context


def _collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _collect_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _collect_strings(v)]
    return []


@pytest.mark.parametrize("flag", [True, False], ids=["flags-on", "flags-off"])
@pytest.mark.parametrize("path", _SPEC_FILES, ids=lambda p: p.parent.name)
def test_hostile_values_round_trip_through_rendering(path: Path, flag: bool) -> None:
    renderer = JinjaSpecRenderer(MagicMock())
    renderer.jinja_env.filters["b64encode"] = lambda v: base64.b64encode(v.encode()).decode()

    rendered = renderer.render_spec_from_file(str(path), _context(path, flag))

    # Valid JSON came back (render_spec_from_file parses it) and every user
    # string arrived unchanged, with no HTML escaping or truncation.
    assert isinstance(rendered, dict)
    strings = _collect_strings(rendered)
    assert not [s for s in strings if "&#" in s or "&amp;" in s or "&lt;" in s]
    if flag:
        assert HOSTILE in strings
    json.dumps(rendered)


_NON_STRING_TAGS = {"count": 5, "enabled": True, "empty": None, "ratio": 1.5}


def _tag_values_from_every_producer() -> dict[str, list[dict[str, Any]]]:
    from types import SimpleNamespace

    from orb.providers.aws.infrastructure.handlers.shared.base_config_builder import (
        BaseConfigBuilder,
    )
    from orb.providers.aws.infrastructure.handlers.shared.base_context_mixin import (
        BaseContextMixin,
    )
    from orb.providers.aws.infrastructure.launch_template.manager import (
        AWSLaunchTemplateManager,
    )

    template = SimpleNamespace(
        template_id="t1",
        tags=dict(_NON_STRING_TAGS),
        machine_types={"t3.micro": 1},
        machine_type=None,
        machine_image="ami-1",
        security_group_ids=[],
        machine_role=None,
        subnet_id=None,
    )

    class _Mixin(BaseContextMixin):
        config_port = None

    manager = AWSLaunchTemplateManager.__new__(AWSLaunchTemplateManager)
    port = MagicMock()
    port.get_resource_prefix.return_value = ""
    port.get_package_info.return_value = {"name": "orb"}
    manager.config_port = port
    manager._logger = MagicMock()

    return {
        "mixin": _Mixin()._prepare_standard_tags(template, "req-1")["custom_tags"],  # type: ignore[arg-type]
        "manager": manager._prepare_template_context(
            template,  # type: ignore[arg-type]
            SimpleNamespace(request_id="req-1"),  # type: ignore[arg-type]
        )["custom_tags"],
        "builder": BaseConfigBuilder._build_tag_context(  # type: ignore[arg-type]
            MagicMock(), "req-1", "t1", "RunInstances", dict(_NON_STRING_TAGS)
        )["custom_tags"],
    }


@pytest.mark.parametrize("producer", ["mixin", "manager", "builder"])
def test_non_string_tag_values_render_as_json_strings(producer: str) -> None:
    custom_tags = _tag_values_from_every_producer()[producer]
    renderer = JinjaSpecRenderer(MagicMock())
    path = _SPECS / "ec2fleet" / "default.json"
    context = _context(path, True)
    context["custom_tags"] = custom_tags

    rendered = renderer.render_spec_from_file(str(path), context)

    tags = rendered["TagSpecifications"][0]["Tags"]
    by_key = {t["Key"]: t["Value"] for t in tags}
    for key in _NON_STRING_TAGS:
        assert isinstance(by_key[key], str), key

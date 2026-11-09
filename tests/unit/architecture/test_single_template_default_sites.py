"""Allocation-strategy, price-type and on-demand-fallback defaults are defined once.

The sources are the configuration defaults:

- ``template.default_price_type`` and ``template.default_fallback_to_ondemand``
  (global template defaults, ``default_config.json`` and the template schema);
- ``provider_defaults.aws.template_defaults.allocation_strategy`` and
  ``allocation_strategy_on_demand`` (``aws_defaults.json``).

Template default resolution applies them. Any other place that supplies one of
these values when it is missing would bypass the configured layers, so the code
under ``src/orb`` is scanned for such defaults and the shipped JSON defaults for
extra copies.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Iterator

import pytest

from tests.unit.architecture.conftest import SRC_ORB

pytestmark = pytest.mark.unit

PRICE_TYPES = frozenset({"ondemand", "spot", "heterogeneous"})
STRATEGIES = frozenset(
    {
        "capacityOptimized",
        "capacityOptimizedPrioritized",
        "diversified",
        "lowestPrice",
        "priceCapacityOptimized",
        "prioritized",
        "capacity-optimized",
        "capacity-optimized-prioritized",
        "lowest-price",
        "price-capacity-optimized",
        "capacity_optimized",
        "capacity_optimized_prioritized",
        "lowest_price",
        "price_capacity_optimized",
    }
)
DEFAULT_VALUES = PRICE_TYPES | STRATEGIES

# Keys whose missing value must come from the configuration defaults.
_KEY = re.compile(
    r"^(default_)?(price_?type|pricetype|allocation_?strategy(_?on_?demand)?)$", re.IGNORECASE
)
_FALLBACK_KEY = re.compile(r"^(default_)?fallback_?to_?on_?demand$", re.IGNORECASE)

# The one domain field default for price_type, kept equal to the config default.
_PRICE_TYPE_FIELD_SITES = {
    ("domain/template/template_aggregate.py", "Template"),
    ("application/dto/template.py", "TemplateDTO"),
}
# The core fallback field and its config schema default.
_FALLBACK_FIELD_SITES = {
    ("domain/template/template_aggregate.py", "Template"),
    ("application/dto/template.py", "TemplateDTO"),
    ("config/schemas/template_schema.py", "TemplateConfig"),
}
# The schema default of the global price type, which the shipped config repeats.
_SCHEMA_SITES = {("config/schemas/template_schema.py", "TemplateConfig")}
# Files that read a price type back from a provider's machine record, which is
# not a template value.
_MACHINE_RECORD_FILES = {"domain/machine/machine_metadata.py"}


def _key_name(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _is_default_value(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value in DEFAULT_VALUES


def _is_false(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _mentions_fallback(node: ast.AST) -> bool:
    for child in ast.walk(node):
        name = child.attr if isinstance(child, ast.Attribute) else _key_name(child)
        if isinstance(child, ast.Name):
            name = child.id
        if name and _FALLBACK_KEY.match(name):
            return True
    return False


def _mentions_key(node: ast.AST) -> bool:
    for child in ast.walk(node):
        name = child.attr if isinstance(child, ast.Attribute) else _key_name(child)
        if isinstance(child, ast.Name):
            name = child.id
        if name and _KEY.match(name):
            return True
    return False


def _field_default(value: ast.AST) -> ast.AST | None:
    """The default of ``x: T = <value>`` or ``x: T = Field(<default>, ...)``."""
    if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "Field":
        if value.args:
            return value.args[0]
        for kw in value.keywords:
            if kw.arg == "default":
                return kw.value
        return None
    return value


def _violations(path: Path) -> Iterator[str]:
    rel = path.relative_to(SRC_ORB).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for stmt in cls.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            name = stmt.target.id
            default = _field_default(stmt.value) if stmt.value is not None else None
            if default is None:
                continue
            if _KEY.match(name) and _is_default_value(default):
                allowed = _PRICE_TYPE_FIELD_SITES | _SCHEMA_SITES
                if not ("price_type" in name and (rel, cls.name) in allowed):
                    yield f"{rel}:{stmt.lineno} field default {cls.name}.{name}"
            if _FALLBACK_KEY.match(name) and _is_false(default):
                if (rel, cls.name) not in _FALLBACK_FIELD_SITES:
                    yield f"{rel}:{stmt.lineno} field default {cls.name}.{name}"

    for node in ast.walk(tree):
        # x or "<value>" / x or False on a fallback value
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            last = node.values[-1]
            if _is_default_value(last) and any(_mentions_key(v) for v in node.values[:-1]):
                yield f"{rel}:{node.lineno} 'or {last.value!r}'"  # type: ignore[attr-defined]
            if _is_false(last) and any(_mentions_fallback(v) for v in node.values[:-1]):
                yield f"{rel}:{node.lineno} 'or False' on fallback_to_ondemand"
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        method = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        # d.get(k, "<value>"), d.setdefault(k, "<value>"), getattr(o, k, "<value>")
        if method in ("get", "setdefault", "getattr") and len(node.args) >= 2:
            key_index = 1 if method == "getattr" else 0
            if len(node.args) > key_index + 1:
                key = _key_name(node.args[key_index])
                default = node.args[key_index + 1]
                if _KEY.match(key) and _is_default_value(default):
                    yield f"{rel}:{node.lineno} {method}({key!r}, {default.value!r})"  # type: ignore[attr-defined]
                if _FALLBACK_KEY.match(key) and _is_false(default):
                    yield f"{rel}:{node.lineno} {method}({key!r}, False)"

    # if not template.allocation_strategy: <assign a default>
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.UnaryOp)):
            continue
        if not isinstance(node.test.op, ast.Not):
            continue
        operand = node.test.operand
        if not (isinstance(operand, ast.Attribute) and _KEY.match(operand.attr)):
            continue
        for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if isinstance(inner, (ast.Assign, ast.AnnAssign)) or (
                isinstance(inner, ast.Call) and getattr(inner.func, "id", "") == "setattr"
            ):
                yield f"{rel}:{node.lineno} default assigned under 'if not ...{operand.attr}'"
                break
            if isinstance(inner, ast.Call) and getattr(inner.func, "attr", "") == "__setattr__":
                yield f"{rel}:{node.lineno} default assigned under 'if not ...{operand.attr}'"
                break


def _python_files() -> list[Path]:
    return sorted(
        p
        for p in SRC_ORB.rglob("*.py")
        if "__pycache__" not in p.parts
        and p.relative_to(SRC_ORB).as_posix() not in _MACHINE_RECORD_FILES
    )


def test_no_default_site_in_the_source() -> None:
    found = [v for path in _python_files() for v in _violations(path)]
    assert not found, (
        "allocation-strategy, price-type or fallback_to_ondemand defaults outside the "
        "configuration defaults:\n" + "\n".join(found)
    )


# ---------------------------------------------------------------------------
# The shipped JSON defaults
# ---------------------------------------------------------------------------

_JSON_KEYS = re.compile(
    r'"(default_)?(price_type|allocation_strategy|allocation_strategy_on_demand|fallback_to_ondemand)"'
)


def _shipped_default_files() -> list[Path]:
    return sorted(
        p
        for p in SRC_ORB.rglob("*.json")
        if p.name == "default_config.json" or p.name.endswith("_defaults.json")
    )


def test_shipped_defaults_define_each_value_once() -> None:
    sites: dict[str, list[str]] = {}
    for path in _shipped_default_files():
        rel = path.relative_to(SRC_ORB).as_posix()
        for match in _JSON_KEYS.finditer(path.read_text(encoding="utf-8")):
            sites.setdefault(match.group(0).strip('"'), []).append(rel)

    assert sites == {
        "default_price_type": ["config/default_config.json"],
        "default_fallback_to_ondemand": ["config/default_config.json"],
        "allocation_strategy": ["providers/aws/config/aws_defaults.json"],
        "allocation_strategy_on_demand": ["providers/aws/config/aws_defaults.json"],
    }


def test_strategy_defaults_sit_in_the_aws_template_defaults() -> None:
    data = json.loads((SRC_ORB / "providers/aws/config/aws_defaults.json").read_text())
    defaults = data["provider"]["provider_defaults"]["aws"]
    assert defaults["template_defaults"]["allocation_strategy"] == "priceCapacityOptimized"
    assert defaults["template_defaults"]["allocation_strategy_on_demand"] == "lowestPrice"
    assert "price_type" not in defaults["template_defaults"]


def test_bundled_oci_examples_do_not_pin_the_fallback_off() -> None:
    text = (SRC_ORB / "providers/oci/config/oci_templates.json").read_text(encoding="utf-8")
    assert not re.search(r'"fallback_to_ondemand"\s*:\s*false', text)


def test_domain_price_type_default_equals_the_config_default() -> None:
    from orb.application.dto.template import TemplateDTO
    from orb.config.schemas.template_schema import TemplateConfig
    from orb.domain.template.template_aggregate import Template

    config = json.loads((SRC_ORB / "config/default_config.json").read_text())["template"]
    shipped = config["default_price_type"]
    assert Template.model_fields["price_type"].default == shipped
    assert TemplateDTO.model_fields["price_type"].default == shipped
    assert TemplateConfig.model_fields["default_price_type"].default == shipped
    assert config["default_fallback_to_ondemand"] is False
    assert TemplateConfig.model_fields["default_fallback_to_ondemand"].default is False

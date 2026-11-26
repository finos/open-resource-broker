"""Domain pydantic convention.

The domain layer uses pydantic for aggregates, value objects and events. This
test pins the set of domain modules that import pydantic, so adding a new one
is a deliberate, reviewed decision, and bans the pieces that do not belong in
the domain: settings classes, the v1 compatibility layer, and imports of the
configuration, infrastructure and provider layers.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture.conftest import SRC_ORB, collect_python_files

_DOMAIN = SRC_ORB / "domain"

_ALLOWED_PYDANTIC_MODULES = frozenset(
    {
        "base/diagnostic.py",
        "base/entity.py",
        "base/events/base_events.py",
        "base/events/domain_events.py",
        "base/events/provider_events.py",
        "base/value_objects.py",
        "machine/aggregate.py",
        "machine/machine_identifiers.py",
        "machine/machine_metadata.py",
        "request/aggregate.py",
        "request/request_identifiers.py",
        "request/request_metadata.py",
        "template/template_aggregate.py",
    }
)

_BANNED_MODULES = (
    "pydantic_settings",
    "pydantic.v1",
    "orb.config",
    "orb.infrastructure",
    "orb.providers",
)
_BANNED_NAMES = frozenset({"BaseSettings"})


def _is_pydantic(module: str) -> bool:
    return module == "pydantic" or module.startswith("pydantic.")


def _is_banned_module(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in _BANNED_MODULES)


def _pydantic_import_lines(source: str) -> int:
    """Count import statements that bring in pydantic."""
    count = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            count += sum(1 for a in node.names if _is_pydantic(a.name))
        elif isinstance(node, ast.ImportFrom) and node.module and _is_pydantic(node.module):
            count += 1
    return count


def _banned_findings(source: str) -> list[str]:
    """Return the banned imports found in *source*."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if _is_banned_module(a.name)]
        elif isinstance(node, ast.ImportFrom):
            if node.module and _is_banned_module(node.module):
                found.append(node.module)
            found += [a.name for a in node.names if a.name in _BANNED_NAMES]
    return found


def _domain_sources() -> dict[str, str]:
    return {
        f.relative_to(_DOMAIN).as_posix(): f.read_text(encoding="utf-8")
        for f in collect_python_files(_DOMAIN)
    }


@pytest.mark.unit
@pytest.mark.architecture
def test_pydantic_importers_match_allowlist() -> None:
    importers = {rel for rel, src in _domain_sources().items() if _pydantic_import_lines(src)}
    assert len(_ALLOWED_PYDANTIC_MODULES) == 13
    assert importers == _ALLOWED_PYDANTIC_MODULES, (
        f"new: {sorted(importers - _ALLOWED_PYDANTIC_MODULES)}, "
        f"gone: {sorted(_ALLOWED_PYDANTIC_MODULES - importers)}. "
        "Update the allowlist and docs/root/architecture/domain_pydantic_convention.md "
        "if this is intentional."
    )


@pytest.mark.unit
@pytest.mark.architecture
def test_each_allowed_module_has_one_pydantic_import() -> None:
    sources = _domain_sources()
    extra = {
        rel: n
        for rel in _ALLOWED_PYDANTIC_MODULES
        if (n := _pydantic_import_lines(sources[rel])) != 1
    }
    assert not extra, f"modules with more than one pydantic import statement: {extra}"


@pytest.mark.unit
@pytest.mark.architecture
def test_domain_has_no_banned_imports() -> None:
    found = {rel: hits for rel, src in _domain_sources().items() if (hits := _banned_findings(src))}
    assert not found, f"banned imports in domain: {found}"


@pytest.mark.unit
@pytest.mark.architecture
@pytest.mark.parametrize(
    "source",
    [
        "from pydantic_settings import BaseSettings\n",
        "import pydantic_settings\n",
        "from pydantic.v1 import BaseModel\n",
        "from pydantic import BaseSettings\n",
        "from orb.config.schemas import AppConfig\n",
        "import orb.infrastructure.logging\n",
        "def f():\n    from orb.providers.aws import x\n",
    ],
)
def test_banned_import_detector_sees_each_form(source: str) -> None:
    assert _banned_findings(source)


@pytest.mark.unit
@pytest.mark.architecture
def test_pydantic_import_counter_sees_each_form() -> None:
    assert _pydantic_import_lines("import pydantic\n") == 1
    assert (
        _pydantic_import_lines("from pydantic import Field\nfrom pydantic.fields import X\n") == 2
    )
    assert _pydantic_import_lines("def f():\n    from pydantic import Field\n") == 1
    assert _pydantic_import_lines("import pydantic_settings\n") == 0

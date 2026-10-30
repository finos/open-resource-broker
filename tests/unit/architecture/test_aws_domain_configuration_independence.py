"""The AWS domain layer must not depend on the AWS configuration layer.

Domain value objects validate against the stdlib-only ``naming_patterns`` leaf
module; configuration models use the same module for their defaults. Imports
therefore flow ``configuration -> domain`` only, and the two packages form no
import cycle.
"""

from __future__ import annotations

import ast
from pathlib import Path

from orb.providers.aws.domain.template import naming_patterns
from tests.unit.architecture.conftest import SRC_ORB, collect_python_files

_AWS = SRC_ORB / "providers" / "aws"
_DOMAIN = _AWS / "domain"
_CONFIGURATION = _AWS / "configuration"
_DOMAIN_PKG = "orb.providers.aws.domain"
_CONFIGURATION_PKG = "orb.providers.aws.configuration"


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC_ORB.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(path: Path, own_name: str, known: set[str]) -> set[str]:
    """Return known module names imported anywhere in *path* (including lazy imports)."""
    is_package = path.name == "__init__.py"
    own_parts = own_name.split(".")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(own_parts) - (node.level - (1 if is_package else 0))
                base = ".".join(own_parts[:keep] + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if not base:
                continue
            candidates = [base] + [f"{base}.{alias.name}" for alias in node.names]
        else:
            continue
        found.update(name for name in candidates if name in known)
    return found


def _under(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def test_domain_never_imports_configuration() -> None:
    offenders = []
    for path in collect_python_files(_DOMAIN):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders.extend(
                f"{path.relative_to(SRC_ORB)}:{node.lineno} imports {name}"
                for name in names
                if _under(name, _CONFIGURATION_PKG)
            )
    assert offenders == []


def test_domain_and_configuration_form_no_import_cycle() -> None:
    files = collect_python_files(_DOMAIN) + collect_python_files(_CONFIGURATION)
    modules = {_module_name(path): path for path in files}
    known = set(modules)
    graph = {name: _imported_modules(path, name, known) - {name} for name, path in modules.items()}

    def reachable(start: str) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            for nxt in graph[stack.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    crossing = sorted(
        name
        for name in modules
        if _under(name, _DOMAIN_PKG)
        and any(_under(other, _CONFIGURATION_PKG) for other in reachable(name))
        and any(
            _under(other, _CONFIGURATION_PKG) and name in reachable(other)
            for other in reachable(name)
        )
    )
    assert crossing == []


def test_naming_patterns_leaf_is_standard_library_only() -> None:
    tree = ast.parse(Path(naming_patterns.__file__).read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert imports == []

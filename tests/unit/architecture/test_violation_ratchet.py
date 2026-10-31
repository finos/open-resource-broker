"""Task 1748: Violation ratchet counter test.

Counts current violations for each boundary category and asserts the count
has not increased beyond the stored ceiling in violation_counts.json.

This allows incremental cleanup: as violations are fixed, update
violation_counts.json to the new (lower) count to lock in the improvement.
"""

from __future__ import annotations

import ast
import collections
import json
from pathlib import Path

import pytest

from tests.unit.architecture.conftest import (
    EXCEPTION_PATHS,
    SRC_ORB,
    collect_python_files,
    extract_imports,
)

_COUNTS_FILE = Path(__file__).parent / "violation_counts.json"

_TOP_PACKAGES = frozenset(
    [
        "domain",
        "application",
        "infrastructure",
        "interface",
        "api",
        "cli",
        "providers",
        "config",
        "mcp",
        "monitoring",
        "sdk",
    ]
)


def _load_ceilings() -> dict[str, int]:
    return json.loads(_COUNTS_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


def _count_domain_forbidden() -> int:
    forbidden = ("orb.infrastructure", "orb.providers", "orb.interface", "orb.api", "orb.cli")
    count = 0
    for f in collect_python_files(SRC_ORB / "domain"):
        for imp in extract_imports(f):
            if any(imp == p or imp.startswith(p + ".") for p in forbidden):
                count += 1
    return count


def _count_application_forbidden() -> int:
    forbidden = ("orb.infrastructure", "orb.providers", "orb.interface", "orb.api", "orb.cli")
    count = 0
    for f in collect_python_files(SRC_ORB / "application"):
        for imp in extract_imports(f):
            if any(imp == p or imp.startswith(p + ".") for p in forbidden):
                count += 1
    return count


def _count_provider_leaks() -> int:
    providers_dir = SRC_ORB / "providers"
    count = 0
    for f in collect_python_files(SRC_ORB):
        if f.is_relative_to(providers_dir) or str(f) in EXCEPTION_PATHS:
            continue
        for imp in extract_imports(f):
            if imp == "orb.providers" or imp.startswith("orb.providers."):
                count += 1
    return count


def _count_scheduler_leaks() -> int:
    scheduler_dir = SRC_ORB / "infrastructure" / "scheduler"
    keywords = ("apscheduler", "backgroundscheduler", "asyncioscheduler")
    count = 0
    for f in collect_python_files(SRC_ORB):
        if f.is_relative_to(scheduler_dir) or str(f) in EXCEPTION_PATHS:
            continue
        for imp in extract_imports(f):
            if any(kw in imp.lower() for kw in keywords):
                count += 1
    return count


def _count_storage_leaks() -> int:
    storage_dir = SRC_ORB / "infrastructure" / "storage"
    providers_dir = SRC_ORB / "providers"
    fragments = ("boto3.dynamodb", "boto3.resources", "botocore.exceptions", "sqlalchemy")
    count = 0
    for f in collect_python_files(SRC_ORB):
        if (
            f.is_relative_to(storage_dir)
            or f.is_relative_to(providers_dir)
            or str(f) in EXCEPTION_PATHS
        ):
            continue
        for imp in extract_imports(f):
            if any(imp == frag or imp.startswith(frag + ".") for frag in fragments):
                count += 1
    return count


def _count_domain_pydantic() -> int:
    count = 0
    for f in collect_python_files(SRC_ORB / "domain"):
        for imp in extract_imports(f):
            if imp == "pydantic" or imp.startswith("pydantic."):
                count += 1
    return count


def _count_interface_provider() -> int:
    iface_dirs = [SRC_ORB / "interface", SRC_ORB / "api", SRC_ORB / "cli"]
    count = 0
    for d in iface_dirs:
        for f in collect_python_files(d):
            for imp in extract_imports(f):
                if imp == "orb.providers" or imp.startswith("orb.providers."):
                    count += 1
    return count


def _count_cycles() -> int:
    graph: dict[str, set[str]] = collections.defaultdict(set)
    for f in collect_python_files(SRC_ORB):
        parts = f.relative_to(SRC_ORB).parts
        if not parts:
            continue
        src_pkg = parts[0]
        if src_pkg not in _TOP_PACKAGES:
            continue
        for imp in extract_imports(f):
            if imp.startswith("orb."):
                rest = imp[4:].split(".")[0]
                if rest in _TOP_PACKAGES and rest != src_pkg:
                    graph[src_pkg].add(rest)

    cycles: list[list[str]] = []
    visited: set[str] = set()
    rec_stack: set[str] = set()
    path: list[str] = []
    seen_keys: set[tuple[str, ...]] = set()

    def dfs(node: str) -> None:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        for neighbor in sorted(graph.get(node, [])):
            if neighbor not in visited:
                dfs(neighbor)
            elif neighbor in rec_stack:
                start = path.index(neighbor)
                cycle = path[start:] + [neighbor]
                key = tuple(sorted(set(cycle)))
                if key not in seen_keys:
                    seen_keys.add(key)
                    cycles.append(cycle)
        path.pop()
        rec_stack.discard(node)

    for node in sorted(graph.keys()):
        if node not in visited:
            dfs(node)
    return len(cycles)


def _count_cli_infrastructure() -> int:
    count = 0
    for f in collect_python_files(SRC_ORB / "cli"):
        for imp in extract_imports(f):
            if imp == "orb.infrastructure" or imp.startswith("orb.infrastructure."):
                count += 1
    return count


_LOCATOR_METHODS = frozenset({"get", "has", "get_optional"})
_LOCATOR_FUNCTIONS = frozenset({"get_container", "get_event_bus", "get_logger_port"})


def _is_container_expr(node: ast.expr) -> bool:
    """True for ``container``, ``self.container``, ``self._container`` and similar."""
    if isinstance(node, ast.Name):
        return node.id.lower().endswith("container")
    if isinstance(node, ast.Attribute):
        return node.attr.lower().endswith("container")
    return False


def _mentions_container_port(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return "ContainerPort" in annotation.value
    return any(
        (isinstance(n, ast.Name) and n.id == "ContainerPort")
        or (isinstance(n, ast.Attribute) and n.attr == "ContainerPort")
        for n in ast.walk(annotation)
    )


def _locator_sites_in_source(source: str) -> int:
    count = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _LOCATOR_METHODS
                and _is_container_expr(func.value)
            ):
                count += 1
            elif isinstance(func, ast.Name) and func.id in _LOCATOR_FUNCTIONS:
                count += 1
        elif isinstance(node, ast.arg) and _mentions_container_port(node.annotation):
            count += 1
    return count


def _count_application_service_locators() -> int:
    """Count service-locator uses under ``application/``.

    Counted: ``<container>.get/has/get_optional(...)`` calls, calls to the
    ``get_container``/``get_event_bus``/``get_logger_port`` helpers, and any
    function parameter typed ``ContainerPort`` (constructor or module-level
    helper), which would let a container be passed through and defeat the
    call check.
    """
    count = 0
    for f in collect_python_files(SRC_ORB / "application"):
        try:
            count += _locator_sites_in_source(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
    return count


# ---------------------------------------------------------------------------
# Ratchet tests
# ---------------------------------------------------------------------------

_COUNTERS = {
    "domain_forbidden_imports": _count_domain_forbidden,
    "application_forbidden_imports": _count_application_forbidden,
    "provider_leaks_non_di": _count_provider_leaks,
    "scheduler_leaks": _count_scheduler_leaks,
    "storage_leaks_non_storage": _count_storage_leaks,
    "domain_pydantic_violations": _count_domain_pydantic,
    "interface_provider_direct_imports": _count_interface_provider,
    "circular_dependency_cycles": _count_cycles,
    "cli_infrastructure_direct_imports": _count_cli_infrastructure,
    "application_service_locator_sites": _count_application_service_locators,
}


@pytest.mark.parametrize("category", list(_COUNTERS.keys()))
@pytest.mark.unit
@pytest.mark.architecture
def test_violation_count_has_not_increased(category: str) -> None:
    """Violation count for *category* must not exceed the stored ceiling."""
    ceilings = _load_ceilings()
    ceiling = ceilings[category]
    current = _COUNTERS[category]()
    assert current <= ceiling, (
        f"Violation ratchet breached for '{category}': "
        f"current={current} > ceiling={ceiling}. "
        f"Fix the new violations or update violation_counts.json if this is intentional."
    )


@pytest.mark.unit
@pytest.mark.architecture
def test_service_locator_counter_detects_each_form() -> None:
    """The locator counter must see every form it is meant to ratchet."""
    source = """
from orb.domain.base.ports import ContainerPort


def _resolve(container: ContainerPort):
    return container.get(object)


class Handler:
    def __init__(self, container: "ContainerPort"):
        self._container = container

    def run(self):
        if self._container.has(int):
            return self._container.get(int)
        return get_container().get_optional(int)
"""
    # two ContainerPort params, get + has + get on containers, get_container call
    assert _locator_sites_in_source(source) == 6
    assert _locator_sites_in_source("def f(port):\n    return port.get(1)\n") == 0

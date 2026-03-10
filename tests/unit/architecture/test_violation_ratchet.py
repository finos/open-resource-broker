"""Task 1748: Violation ratchet counter test.

Counts current violations for each boundary category and asserts the count
has not increased beyond the stored ceiling in violation_counts.json.

This allows incremental cleanup: as violations are fixed, update
violation_counts.json to the new (lower) count to lock in the improvement.
"""

from __future__ import annotations

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
    ["domain", "application", "infrastructure", "interface", "api", "cli",
     "providers", "config", "mcp", "monitoring", "sdk"]
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
        if f.is_relative_to(storage_dir) or f.is_relative_to(providers_dir) or str(f) in EXCEPTION_PATHS:
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

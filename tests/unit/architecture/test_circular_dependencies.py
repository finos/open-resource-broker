"""Task 1746: Cross-layer circular dependency test.

Builds a module-level dependency graph from all src/orb/ files and asserts
there are no circular dependencies between top-level packages.

Current known cycles are whitelisted so the test passes today while preventing
any NEW cycles from being introduced.
"""

from __future__ import annotations

import collections

from tests.unit.architecture.conftest import (
    SRC_ORB,
    collect_python_files,
    extract_imports,
)

_TOP_PACKAGES = frozenset(
    ["domain", "application", "infrastructure", "interface", "api", "cli",
     "providers", "config", "mcp", "monitoring", "sdk"]
)

# Known cycles that exist in the current codebase.  Each cycle is stored as a
# frozenset of the participating package names (direction-independent, duplicate-free).
# A detected cycle is "known" if its node-set is a subset of any known cycle node-set.
_KNOWN_CYCLE_NODESETS: list[frozenset[str]] = [
    frozenset({"api", "application", "config", "infrastructure"}),
    frozenset({"application", "config", "infrastructure"}),
    frozenset({"application", "config", "infrastructure", "cli"}),
    frozenset({"infrastructure", "cli"}),
    frozenset({"api", "application", "config", "infrastructure", "cli", "interface"}),
    frozenset({"application", "config", "infrastructure", "cli", "interface"}),
    frozenset({"cli", "interface"}),
    frozenset({"config", "infrastructure", "cli", "interface"}),
    frozenset({"infrastructure", "cli", "interface"}),
    frozenset({"application", "config", "infrastructure", "cli", "interface", "mcp", "sdk"}),
    frozenset({"infrastructure", "cli", "interface", "mcp", "sdk"}),
    frozenset({"config", "infrastructure", "cli", "interface", "monitoring"}),
    frozenset({"infrastructure", "cli", "interface", "monitoring"}),
    frozenset({"application", "config", "infrastructure", "cli", "interface", "providers"}),
    frozenset({"cli", "interface", "providers"}),
    frozenset({"config", "infrastructure", "cli", "interface", "providers"}),
    frozenset({"infrastructure", "cli", "interface", "providers"}),
    frozenset({"config", "infrastructure"}),
]


def _build_graph() -> dict[str, set[str]]:
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
    return dict(graph)


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
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
    return cycles


def test_no_new_circular_dependencies() -> None:
    """No new circular dependencies may be introduced between top-level packages."""
    graph = _build_graph()
    cycles = _find_cycles(graph)

    new_cycles = []
    for cycle in cycles:
        cycle_nodeset = frozenset(cycle)  # includes the repeated last node, frozenset dedupes
        is_known = any(cycle_nodeset <= known for known in _KNOWN_CYCLE_NODESETS)
        if not is_known:
            new_cycles.append(" -> ".join(cycle))

    assert new_cycles == [], (
        f"NEW circular dependencies detected between top-level packages:\n"
        + "\n".join(f"  {c}" for c in new_cycles)
    )

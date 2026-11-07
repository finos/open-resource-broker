"""The clock seam is complete and the sleep routing list is honest."""

from __future__ import annotations

import ast
from pathlib import Path

import orb
from tests.providers.lifecycle.clocking import (
    CLOCK_SEAM_MODULES,
    SLEEP_SITES,
    UNROUTED_SLEEPS,
    RealSleepGuard,
)

SRC = Path(orb.__file__).resolve().parent.parent
_FORBIDDEN = {
    ("datetime", "now"),
    ("time", "monotonic"),
    ("time", "sleep"),
    ("asyncio", "sleep"),
}
_SCANNED_ROOTS = (
    "orb/application",
    "orb/infrastructure/resilience",
    "orb/providers/aws",
    "orb/providers/k8s/infrastructure",
)


def _default_nodes(tree: ast.AST) -> set[int]:
    nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                if default is not None:
                    nodes |= {id(n) for n in ast.walk(default)}
    return nodes


def _direct_calls(path: Path, only_sleep: bool = False) -> list[tuple[int, str]]:
    """Direct uses of the forbidden clock functions, outside default argument values.

    A bare reference such as ``sleep or time.sleep`` counts as a use, because it
    resolves the standard library function at call time.
    """
    tree = ast.parse(path.read_text())
    skip = _default_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and id(node) not in skip
        ):
            key = (node.value.id, node.attr)
            if key in _FORBIDDEN and (not only_sleep or key[1] == "sleep"):
                found.append((node.lineno, f"{key[0]}.{key[1]}"))
    return found


def test_seam_modules_take_time_from_the_clock():
    offenders = {
        module: calls for module in CLOCK_SEAM_MODULES if (calls := _direct_calls(SRC / module))
    }
    assert not offenders, f"direct clock reads outside the seam: {offenders}"


def test_every_direct_sleep_is_routed_or_accounted_for():
    routed = {module.replace(".", "/") + ".py" for module in SLEEP_SITES}
    seam = set(CLOCK_SEAM_MODULES)
    unaccounted = []
    for root in _SCANNED_ROOTS:
        for path in sorted((SRC / root).rglob("*.py")):
            relative = str(path.relative_to(SRC))
            if relative in routed or relative in seam or relative in UNROUTED_SLEEPS:
                continue
            if _direct_calls(path, only_sleep=True):
                unaccounted.append(relative)
    assert not unaccounted, (
        f"sleeps neither routed to the fake clock nor accounted for: {unaccounted}"
    )


def test_routed_sleep_sites_still_sleep_directly():
    stale = []
    for module, names in SLEEP_SITES.items():
        calls = {name for _, name in _direct_calls(SRC / (module.replace(".", "/") + ".py"), True)}
        for name in names:
            if f"{name}.sleep" not in calls:
                stale.append((module, name))
    assert not stale, f"entries that no longer sleep through the listed module: {stale}"


def test_unrouted_sleeps_exist():
    missing = [m for m in UNROUTED_SLEEPS if not _direct_calls(SRC / m, only_sleep=True)]
    assert not missing, f"stale unrouted entries: {missing}"


def test_real_sleep_guard_records_long_sleeps(monkeypatch):
    guard = RealSleepGuard()
    guard.install(monkeypatch)
    import time

    time.sleep(0.01)
    assert guard.violations == []
    time.sleep(RealSleepGuard.LIMIT + 0.01)
    assert [v[0] for v in guard.violations] == ["time.sleep"]
    guard.violations.clear()  # the autouse guard must not fail this test itself

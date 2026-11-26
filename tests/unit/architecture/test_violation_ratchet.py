"""Task 1748: Violation ratchet counter test.

Counts current violations for each boundary category and asserts the count
has not increased beyond the stored ceiling in violation_counts.json.

This allows incremental cleanup: as violations are fixed, update
violation_counts.json to the new (lower) count to lock in the improvement.
"""

from __future__ import annotations

import ast
import collections
import io
import json
import re
import tokenize
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


def _count_interface_service_locators() -> int:
    """Count service-locator uses under ``interface/`` (same rules as ``application/``)."""
    count = 0
    for f in collect_python_files(SRC_ORB / "interface"):
        try:
            count += _locator_sites_in_source(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
    return count


_SUPPRESSION = re.compile(
    r"#\s*(?:type:\s*ignore|pyright:\s*ignore|noqa|nosec)\b"
    r"(?:\s*\[[^\]]*\]|\s*:\s*[A-Za-z]+\d+(?:\s*,\s*[A-Za-z]+\d+)*)?"
    r"(?P<rest>.*)$"
)


def _reasonless_suppressions_in_source(source: str) -> int:
    """Count suppression comments that carry no explanation.

    Only real comments are examined (``tokenize``), so strings that mention a
    directive do not count. A reason is any text left after the directive and
    its optional bracket or code list, ignoring separators.
    """
    count = 0
    # An untokenizable file raises, so a broken file cannot silently count as zero.
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        match = _SUPPRESSION.search(tok.string)
        if match and not match.group("rest").strip(" \t#-:;,\u2013\u2014"):
            count += 1
    return count


def _count_reasonless_suppressions() -> int:
    count = 0
    for f in collect_python_files(SRC_ORB):
        count += _reasonless_suppressions_in_source(f.read_text(encoding="utf-8"))
    return count


_STDLIB_LOGGER_EXEMPT = (
    SRC_ORB / "api",
    SRC_ORB / "cli",
    SRC_ORB / "infrastructure" / "logging",
    SRC_ORB / "k8s_legacy",
)


def _uses_stdlib_logging(source: str) -> bool:
    """True when *source* imports ``logging`` (at any depth) or calls ``getLogger``."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(a.name == "logging" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "logging":
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "getLogger") or (
                isinstance(func, ast.Name) and func.id == "getLogger"
            ):
                return True
    return False


def _count_stdlib_logger_files() -> int:
    """Count files that use the stdlib logger outside the logging boundary.

    Code should receive a ``LoggingPort`` rather than build its own logger.
    ``api/`` and ``cli/`` are process entry points, ``infrastructure/logging``
    implements the port, and ``k8s_legacy`` is maintenance-mode.

    ``application/machine/result_mapping.py`` is a pure mapping module with no
    injected dependencies and stays on the baseline until its callers can pass
    a logger.
    """
    count = 0
    for f in collect_python_files(SRC_ORB):
        if any(f.is_relative_to(d) for d in _STDLIB_LOGGER_EXEMPT):
            continue
        try:
            if _uses_stdlib_logging(f.read_text(encoding="utf-8")):
                count += 1
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
    "interface_provider_direct_imports": _count_interface_provider,
    "circular_dependency_cycles": _count_cycles,
    "cli_infrastructure_direct_imports": _count_cli_infrastructure,
    "application_service_locator_sites": _count_application_service_locators,
    "stdlib_logger_files_outside_boundary": _count_stdlib_logger_files,
    "interface_service_locator_sites": _count_interface_service_locators,
    "reasonless_suppressions": _count_reasonless_suppressions,
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


@pytest.mark.unit
@pytest.mark.architecture
@pytest.mark.parametrize(
    "source",
    [
        "import logging\n",
        "from logging import getLogger\n",
        "x = getLogger('a')\n",
        "def f():\n    import logging as _l\n    return _l.getLogger('a')\n",
    ],
)
def test_stdlib_logger_counter_detects_each_form(source: str) -> None:
    assert _uses_stdlib_logging(source)


@pytest.mark.unit
@pytest.mark.architecture
def test_stdlib_logger_counter_ignores_unrelated_source() -> None:
    assert not _uses_stdlib_logging("def f(logger):\n    logger.info('x')\n")


@pytest.mark.unit
@pytest.mark.architecture
@pytest.mark.parametrize(
    "line",
    [
        "x = 1  # type: ignore",
        "x = 1  # type: ignore[attr-defined]",
        "x = 1  # pyright: ignore",
        "x = 1  # pyright: ignore[reportGeneralTypeIssues]",
        "x = 1  # noqa",
        "x = 1  # noqa: E501",
        "x = 1  # noqa: E501, F401",
        "x = 1  # nosec",
        "x = 1  # nosec - ",
    ],
)
def test_reasonless_suppression_counter_detects_each_form(line: str) -> None:
    assert _reasonless_suppressions_in_source(line + "\n") == 1


@pytest.mark.unit
@pytest.mark.architecture
@pytest.mark.parametrize(
    "line",
    [
        "x = 1  # type: ignore[attr-defined]  # stub has no attribute",
        "x = 1  # noqa: E501 - long URL",
        "x = 1  # noqa: F401  re-exported for callers",
        "x = 1  # type: ignore  # third-party stubs are incomplete",
        "x = 'a  # noqa'",
        "x = 1  # an ordinary comment",
    ],
)
def test_reasonless_suppression_counter_ignores_explained_and_non_comments(line: str) -> None:
    assert _reasonless_suppressions_in_source(line + "\n") == 0


@pytest.mark.unit
@pytest.mark.architecture
def test_reasonless_suppression_counter_raises_on_untokenizable_source() -> None:
    with pytest.raises((tokenize.TokenError, IndentationError, SyntaxError)):
        _reasonless_suppressions_in_source("x = (1,\n")

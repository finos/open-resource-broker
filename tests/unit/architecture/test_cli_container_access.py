"""Pin where ``orb.cli`` may reach for the global container.

CLI handlers receive their collaborators through constructors; the only places
in ``cli/`` that call ``get_container`` are the dispatch boundary and a few
process-level helpers. A new call anywhere else fails this test, so a handler
cannot quietly reintroduce a lookup.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from tests.unit.architecture.conftest import SRC_ORB, collect_python_files

CLI_DIR = SRC_ORB / "cli"

# (file relative to cli/, enclosing function path) -> number of get_container() calls.
ALLOWED_GET_CONTAINER_CALLS: dict[tuple[str, str], int] = {
    ("main.py", "_drain_events"): 1,
    ("main.py", "_start_update_check"): 1,
    # scheduler override and restore, plus the init early path
    ("main.py", "main"): 3,
    # bind() and the bus handler both resolve through this one helper
    ("registry.py", "_current_container"): 1,
    ("router.py", "execute_command"): 1,
}


def _is_get_container(node: ast.Call) -> bool:
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "get_container") or (
        isinstance(func, ast.Attribute) and func.attr == "get_container"
    )


def _get_container_calls(path: Path) -> list[tuple[str, str]]:
    """Return ``(file, enclosing function path)`` for each ``get_container()`` call."""
    found: list[tuple[str, str]] = []
    rel = path.relative_to(CLI_DIR).as_posix()

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = stack
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = [*stack, child.name]
            if isinstance(child, ast.Call) and _is_get_container(child):
                found.append((rel, ".".join(stack)))
            visit(child, inner)

    visit(ast.parse(path.read_text(encoding="utf-8")), [])
    return found


@pytest.mark.unit
@pytest.mark.architecture
def test_cli_get_container_calls_match_allowlist() -> None:
    actual: Counter[tuple[str, str]] = Counter()
    for path in collect_python_files(CLI_DIR):
        actual.update(_get_container_calls(path))

    assert dict(actual) == ALLOWED_GET_CONTAINER_CALLS, (
        "get_container() calls in orb/cli changed. Pass collaborators through a "
        "handler class constructor; only extend the allowlist for a new dispatch "
        "boundary or process-level helper."
    )


@pytest.mark.unit
@pytest.mark.architecture
def test_build_registry_never_touches_the_container() -> None:
    registry = CLI_DIR / "registry.py"
    tree = ast.parse(registry.read_text(encoding="utf-8"))
    build = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_registry"
    )
    assert not [n for n in ast.walk(build) if isinstance(n, ast.Call) and _is_get_container(n)]
    assert not build.args.args, "build_registry() must stay argument-free"

"""The package supports Python 3.10, so source must not use newer runtime APIs.

Covers typing names that need ``typing_extensions``, ``asyncio.timeout`` and
``TaskGroup``, ``ExceptionGroup`` and ``except*``, ``tomllib`` and
``datetime.UTC``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

import orb

NEWER_THAN_310 = frozenset(
    {
        "Self",
        "Never",
        "LiteralString",
        "Required",
        "NotRequired",
        "Unpack",
        "TypeVarTuple",
        "assert_never",
        "assert_type",
        "reveal_type",
        "dataclass_transform",
        "override",
        "TypeAliasType",
    }
)


NEWER_ASYNCIO = frozenset({"timeout", "TaskGroup", "Runner", "timeout_at"})
NEWER_DATETIME = frozenset({"UTC"})
NEWER_BUILTINS = frozenset({"ExceptionGroup", "BaseExceptionGroup"})
NEWER_MODULES = frozenset({"tomllib"})


def _offences(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    # Local names bound by ``import module as alias``, so ``aio.timeout`` is still seen.
    aliases = {
        alias.asname: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.asname
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            table = {
                "typing": NEWER_THAN_310,
                "asyncio": NEWER_ASYNCIO,
                "datetime": NEWER_DATETIME,
            }.get(node.module, frozenset())
            names = sorted(a.name for a in node.names if a.name in table)
            if names:
                found.append((node.lineno, f"from {node.module} import {', '.join(names)}"))
            if node.module in NEWER_MODULES:
                found.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in NEWER_MODULES:
                    found.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module, attr = aliases.get(node.value.id, node.value.id), node.attr
            if (
                (module == "typing" and attr in NEWER_THAN_310)
                or (module == "asyncio" and attr in NEWER_ASYNCIO)
                or (module == "datetime" and attr in NEWER_DATETIME)
                or (module == "builtins" and attr in NEWER_BUILTINS)
            ):
                found.append((node.lineno, f"{module}.{attr}"))
        elif isinstance(node, ast.Name) and node.id in NEWER_BUILTINS:
            found.append((node.lineno, node.id))
        elif isinstance(node, getattr(ast, "TryStar", ())):
            found.append((node.lineno, "except*"))
        elif isinstance(node, getattr(ast, "TypeAlias", ())):
            found.append((node.lineno, "type statement"))
        elif getattr(node, "type_params", None):
            found.append((node.lineno, "PEP 695 type parameters"))
    return found


def _offenders() -> list[str]:
    root = Path(orb.__file__).parent
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.extend(f"{path.relative_to(root)}:{line}: {what}" for line, what in _offences(tree))
    return found


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "from typing import Self",
        "import typing\nx = typing.assert_never",
        "import asyncio\nasync def f():\n    async with asyncio.timeout(1): pass",
        "from asyncio import TaskGroup",
        "import asyncio\nasyncio.TaskGroup()",
        "raise ExceptionGroup('x', [])",
        "import builtins\nbuiltins.ExceptionGroup",
        pytest.param(
            "try:\n    pass\nexcept* ValueError:\n    pass",
            marks=pytest.mark.skipif(
                sys.version_info < (3, 11), reason="except* is a syntax error before 3.11"
            ),
        ),
        "import tomllib",
        "import datetime\ndatetime.UTC",
        "import datetime as dt\ndt.UTC",
        "import asyncio as aio\nasync def f():\n    async with aio.timeout(1): pass",
        "import typing as t\nt.Self",
        "from datetime import UTC",
    ],
)
def test_guard_detects_post_310_apis(source: str) -> None:
    assert _offences(ast.parse(source))


@pytest.mark.unit
@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax needs Python 3.12 to parse")
@pytest.mark.parametrize("source", ["type X = int", "def f[T](x: T) -> T:\n    return x"])
def test_guard_detects_pep695_syntax(source: str) -> None:
    assert _offences(ast.parse(source))


@pytest.mark.unit
def test_no_module_uses_post_310_runtime_apis() -> None:
    assert _offenders() == []

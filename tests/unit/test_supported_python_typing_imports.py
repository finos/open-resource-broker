"""The package supports Python 3.10, so source and tests must not use newer runtime APIs.

Covers typing names that need ``typing_extensions``, ``asyncio.timeout`` and
``TaskGroup``, ``ExceptionGroup`` and ``except*``, ``tomllib``, ``datetime.UTC``,
``enum.StrEnum``, ``itertools.batched``, ``Path.walk`` and ``fromisoformat`` called
with a literal ending in ``Z``.

``Path.walk`` and ``fromisoformat`` cannot be decided from the syntax tree alone, so
those two are best effort: ``Path.walk`` is flagged on ``Path(...)`` and
``pathlib.Path`` receivers, and ``fromisoformat`` only for string literals. An import
guarded by ``try`` / ``except ImportError`` (or ``ModuleNotFoundError``) is a
fallback, not a use, and is exempt.
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
NEWER_ENUM = frozenset({"StrEnum", "ReprEnum", "verify", "member", "nonmember"})
NEWER_ITERTOOLS = frozenset({"batched"})
NEWER_BUILTINS = frozenset({"ExceptionGroup", "BaseExceptionGroup"})
NEWER_MODULES = frozenset({"tomllib"})
_ATTRIBUTE_TABLES = {
    "typing": NEWER_THAN_310,
    "asyncio": NEWER_ASYNCIO,
    "datetime": NEWER_DATETIME,
    "enum": NEWER_ENUM,
    "itertools": NEWER_ITERTOOLS,
    "builtins": NEWER_BUILTINS,
}
_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError"})


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    if caught is None:
        return True
    names = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    return any(isinstance(n, ast.Name) and n.id in _IMPORT_ERRORS for n in names)


def _guarded_nodes(tree: ast.AST) -> set[int]:
    """Ids of nodes in the body of a ``try`` that has an ImportError handler."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(_catches_import_error(h) for h in node.handlers):
            for statement in node.body:
                guarded.update(id(child) for child in ast.walk(statement))
    return guarded


def _is_path_receiver(node: ast.expr) -> bool:
    """True for ``Path(...)``, ``pathlib.Path(...)``, ``Path`` and ``pathlib.Path``."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id == "Path"
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "Path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "pathlib"
    )


def _is_z_isoformat_call(node: ast.Call) -> bool:
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name != "fromisoformat" or not node.args:
        return False
    arg = node.args[0]
    return (
        isinstance(arg, ast.Constant)
        and isinstance(arg.value, str)
        and arg.value[-1:] in ("Z", "z")
    )


def _offences(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    guarded = _guarded_nodes(tree)
    # Local names bound by ``import module as alias``, so ``aio.timeout`` is still seen.
    aliases = {
        alias.asname: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.asname
    }
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            table = _ATTRIBUTE_TABLES.get(node.module, frozenset())
            names = sorted(a.name for a in node.names if a.name in table)
            if names:
                found.append((node.lineno, f"from {node.module} import {', '.join(names)}"))
            if node.module in NEWER_MODULES:
                found.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in NEWER_MODULES:
                    found.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                module = aliases.get(node.value.id, node.value.id)
                if node.attr in _ATTRIBUTE_TABLES.get(module, frozenset()):
                    found.append((node.lineno, f"{module}.{node.attr}"))
                    continue
            if node.attr == "walk" and _is_path_receiver(node.value):
                found.append((node.lineno, "Path.walk"))
        elif isinstance(node, ast.Call) and _is_z_isoformat_call(node):
            found.append((node.lineno, "fromisoformat with a Z suffix"))
        elif isinstance(node, ast.Name) and node.id in NEWER_BUILTINS:
            found.append((node.lineno, node.id))
        elif isinstance(node, getattr(ast, "TryStar", ())):
            found.append((node.lineno, "except*"))
        elif isinstance(node, getattr(ast, "TypeAlias", ())):
            found.append((node.lineno, "type statement"))
        elif getattr(node, "type_params", None):
            found.append((node.lineno, "PEP 695 type parameters"))
    return found


_ROOTS = {"src/orb": Path(orb.__file__).parent, "tests": Path(__file__).resolve().parent.parent}


def _offenders(root: Path) -> list[str]:
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
        "from enum import StrEnum",
        "import enum\nclass C(enum.StrEnum): pass",
        "from itertools import batched",
        "import itertools\nitertools.batched([], 2)",
        "from pathlib import Path\nPath('.').walk()",
        "import pathlib\npathlib.Path('.').walk()",
        "import datetime\ndatetime.datetime.fromisoformat('2025-01-01T00:00:00Z')",
        "from datetime import datetime\ndatetime.fromisoformat('2025-01-01T00:00:00Z')",
        "if True:\n    import tomllib",
    ],
)
def test_guard_detects_post_310_apis(source: str) -> None:
    assert _offences(ast.parse(source))


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "try:\n    import tomllib\nexcept ModuleNotFoundError:\n    import tomli as tomllib",
        "try:\n    from enum import StrEnum\nexcept ImportError:\n    StrEnum = str",
        "try:\n    import tomllib\nexcept (ImportError, ValueError):\n    tomllib = None",
        "from typing_extensions import Self, assert_never",
        "from datetime import timezone\ntimezone.utc",
        "import os\nfor _ in os.walk('.'): pass",
        "from datetime import datetime\ndatetime.fromisoformat('2025-01-01T00:00:00+00:00')",
        "from datetime import datetime\ndatetime.fromisoformat(value)",
    ],
)
def test_guard_accepts_compatible_code(source: str) -> None:
    assert _offences(ast.parse(source)) == []


@pytest.mark.unit
def test_import_fallback_exempts_only_the_try_body() -> None:
    source = "try:\n    pass\nexcept ImportError:\n    import tomllib"
    assert _offences(ast.parse(source))


@pytest.mark.unit
@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax needs Python 3.12 to parse")
@pytest.mark.parametrize("source", ["type X = int", "def f[T](x: T) -> T:\n    return x"])
def test_guard_detects_pep695_syntax(source: str) -> None:
    assert _offences(ast.parse(source))


@pytest.mark.unit
@pytest.mark.parametrize("root", sorted(_ROOTS))
def test_no_module_uses_post_310_runtime_apis(root: str) -> None:
    assert _offenders(_ROOTS[root]) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("root", "expected"),
    [
        ("src/orb", ["__init__.py", "domain/base/utils.py"]),
        ("tests", ["unit/test_supported_python_typing_imports.py", "providers/__init__.py"]),
    ],
)
def test_scanned_roots_are_not_empty(root: str, expected: list[str]) -> None:
    base = _ROOTS[root]
    assert any(base.rglob("*.py"))
    for relative in expected:
        assert (base / relative).is_file(), f"{root} scan would miss {relative}"

"""The storage strategies user guide only names exceptions and imports that exist."""

import builtins
import importlib
import re
from pathlib import Path

import pytest

from orb.infrastructure.storage.json.strategy import JSONStorageStrategy
from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy
from orb.providers.aws.storage.strategy import DynamoDBStorageStrategy

DOC = Path(__file__).resolve().parents[4] / "docs/root/user_guide/storage_strategies.md"

_PY_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)
_ORB_IMPORT = re.compile(r"^from (orb(?:\.\w+)+) import ([\w, ]+)$", re.MULTILINE)
_EXCEPT = re.compile(r"^\s*except (\w+)", re.MULTILINE)


def _python_blocks() -> list[str]:
    return _PY_BLOCK.findall(DOC.read_text(encoding="utf-8"))


def test_orb_imports_in_examples_resolve() -> None:
    for block in _python_blocks():
        for module_name, names in _ORB_IMPORT.findall(block):
            module = importlib.import_module(module_name)
            for name in (n.strip() for n in names.split(",")):
                assert hasattr(module, name), f"{module_name} has no {name}"


def test_caught_exceptions_in_examples_are_defined() -> None:
    for block in _python_blocks():
        imported = {
            name.strip() for _, names in _ORB_IMPORT.findall(block) for name in names.split(",")
        }
        for name in _EXCEPT.findall(block):
            assert name in imported or hasattr(builtins, name), (
                f"example catches {name}, which is neither imported nor a builtin"
            )


@pytest.mark.parametrize(
    "strategy", [JSONStorageStrategy, SQLStorageStrategy, DynamoDBStorageStrategy]
)
def test_versioned_save_used_in_examples_exists(strategy) -> None:
    assert callable(getattr(strategy, "save_versioned", None))
    assert callable(getattr(strategy, "find_by_id", None))

"""Every @event_handler name must be unique and name a real domain event class."""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import orb
from orb.domain.base.events import DomainEvent

SRC_ROOT = Path(next(iter(orb.__path__)))


def _decorated_event_names() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for path in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for deco in node.decorator_list:
                if (
                    isinstance(deco, ast.Call)
                    and isinstance(deco.func, ast.Name)
                    and deco.func.id == "event_handler"
                    and deco.args
                    and isinstance(deco.args[0], ast.Constant)
                    and isinstance(deco.args[0].value, str)
                ):
                    found.append((deco.args[0].value, path))
    return found


def _domain_event_class_names() -> set[str]:
    importlib.import_module("orb.domain.base.events")
    for mod in pkgutil.walk_packages(
        importlib.import_module("orb.domain").__path__, prefix="orb.domain."
    ):
        importlib.import_module(mod.name)
    names: set[str] = set()
    stack = [DomainEvent]
    while stack:
        cls = stack.pop()
        for sub in cls.__subclasses__():
            names.add(sub.__name__)
            stack.append(sub)
    return names


@pytest.mark.unit
@pytest.mark.architecture
class TestEventHandlerRegistration:
    def test_handler_names_are_discovered(self):
        assert _decorated_event_names(), "AST scan found no @event_handler decorators"

    def test_handler_names_are_unique(self):
        names = [n for n, _ in _decorated_event_names()]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert not duplicates, f"duplicate @event_handler names (later one wins): {duplicates}"

    def test_every_handler_name_resolves_to_a_domain_event_class(self):
        known = _domain_event_class_names()
        unknown = sorted(
            f"{name} ({path.relative_to(SRC_ROOT)})"
            for name, path in _decorated_event_names()
            if name not in known
        )
        assert not unknown, f"@event_handler names with no DomainEvent class: {unknown}"

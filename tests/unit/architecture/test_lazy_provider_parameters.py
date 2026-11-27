"""Pin the constructor parameters that take a provider instead of an instance.

A handler that reaches a collaborator inside a guarded block (``try``) or only
optionally receives it as a ``Callable[[], T]`` so the failure to resolve it
surfaces at the same place it always did. Turning such a parameter eager, or an
eager one lazy, changes which failures are reported where, so every
``Callable[[], T]`` parameter under ``interface/`` must be listed here.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture.conftest import SRC_ORB, collect_python_files

# (class or function, parameter, provided type)
EXPECTED_PROVIDERS: set[tuple[str, str, str]] = {
    ("StorageHandlers", "query_bus_provider", "QueryBusPort"),
    ("StorageHandlers", "config_manager_provider", "ConfigurationManager"),
    ("TemplateReadHandlers", "console_provider", "ConsolePort"),
    ("TemplatesGenerateHandler", "template_service_provider", "TemplateGenerationService"),
    ("TemplatesGenerateHandler", "console_provider", "ConsolePort"),
}


def _provided_type(annotation: ast.expr | None) -> str | None:
    """Return the ``T`` of a ``Callable[[], T]`` annotation, else ``None``."""
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None
    if not (isinstance(annotation, ast.Subscript) and isinstance(annotation.slice, ast.Tuple)):
        return None
    head = annotation.value
    name = head.id if isinstance(head, ast.Name) else getattr(head, "attr", "")
    args = annotation.slice.elts
    if name != "Callable" or len(args) != 2:
        return None
    params, result = args
    if not (isinstance(params, ast.List) and not params.elts):
        return None
    return ast.unparse(result)


def _providers_in(tree: ast.AST) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()

    def visit(node: ast.AST, owner: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, child.name)
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = owner if child.name == "__init__" and owner else child.name
                for arg in [*child.args.args, *child.args.kwonlyargs]:
                    provided = _provided_type(arg.annotation)
                    # Coroutine factories (daemon launch) are not collaborator providers.
                    if provided is not None and not provided.startswith("Coroutine["):
                        found.add((scope, arg.arg, provided))
            visit(child, owner)

    visit(tree, None)
    return found


@pytest.mark.unit
@pytest.mark.architecture
def test_lazy_provider_parameters_are_pinned() -> None:
    actual: set[tuple[str, str, str]] = set()
    for path in collect_python_files(SRC_ORB / "interface"):
        actual |= _providers_in(ast.parse(path.read_text(encoding="utf-8")))

    assert actual == EXPECTED_PROVIDERS, (
        "Callable[[], T] parameters under interface/ changed. A provider keeps a failure to "
        "resolve a collaborator where it always surfaced; update this list only when the "
        "collaborator is (or stops being) reached inside a guarded block."
    )


@pytest.mark.unit
@pytest.mark.architecture
def test_provider_detection_sees_each_annotation_form() -> None:
    source = """
from typing import Callable, Optional
import collections.abc as abc


class A:
    def __init__(self, a: Callable[[], X], b: "Callable[[], Y]", c: abc.Callable[[], Z | None]):
        pass


def f(x: Callable[[int], X], y: Optional[X], z: Callable[[], X]):
    pass
"""
    assert _providers_in(ast.parse(source)) == {
        ("A", "a", "X"),
        ("A", "b", "Y"),
        ("A", "c", "Z | None"),
        ("f", "z", "X"),
    }

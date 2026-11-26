"""The scheduler authoring guide must reference code that exists."""

import ast
import importlib
import re
from functools import cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE = REPO_ROOT / "docs" / "root" / "developer_guide" / "adding_a_scheduler.md"
PORTS_MODULE = "orb.application.ports.scheduler_port"
PORT_NAMES = (
    "SchedulerFormattingPort",
    "SchedulerEnvironmentPort",
    "SchedulerTemplateIOPort",
    "SchedulerRequestParsingPort",
    "SchedulerPort",
)


def _guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_guide_names_every_port():
    text = _guide_text()
    for name in PORT_NAMES:
        assert f"`{name}`" in text


@pytest.mark.parametrize("name", PORT_NAMES)
def test_named_ports_exist(name):
    assert hasattr(importlib.import_module(PORTS_MODULE), name)


def test_cited_source_paths_exist():
    paths = set(re.findall(r"`((?:src|tests)/[\w./<>-]+\.py)`", _guide_text()))
    assert paths
    for rel in sorted(paths):
        if "<" in rel:
            continue
        assert (REPO_ROOT / rel).is_file(), rel


def test_cited_source_directories_exist():
    for rel in sorted(set(re.findall(r"`((?:src|tests)/[\w./<>-]+/)`", _guide_text()))):
        if "<" in rel:
            continue
        assert (REPO_ROOT / rel).is_dir(), rel


def test_registration_functions_exist():
    registration = importlib.import_module("orb.infrastructure.scheduler.registration")
    text = _guide_text()
    for fn in (
        "register_symphony_hostfactory_scheduler",
        "register_default_scheduler",
        "register_slurm_scheduler",
        "register_all_scheduler_types",
        "register_active_scheduler_only",
    ):
        assert f"`{fn}`" in text
        assert callable(getattr(registration, fn))


def test_documented_types_match_schema():
    from orb.config.schemas.scheduler_schema import SchedulerConfig

    literal_args = SchedulerConfig.model_fields["type"].annotation.__args__
    for name in literal_args:
        assert f"`{name}`" in _guide_text()


# Backticked names in the guide that are not classes or constants defined in src/orb.
NON_CODE_NAMES = {"Literal", "None", "TypeError"}


@cache
def _source_index() -> tuple[dict[str, tuple[set[str], set[str]]], set[str]]:
    """Map class name to (member names, base names), and collect module-level names."""
    classes: dict[str, tuple[set[str], set[str]]] = {}
    module_names: set[str] = set()
    for path in (REPO_ROOT / "src" / "orb").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                module_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                module_names.add(node.target.id)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            members, bases = classes.setdefault(node.name, (set(), set()))
            bases.update(b.id for b in node.bases if isinstance(b, ast.Name))
            bases.update(b.attr for b in node.bases if isinstance(b, ast.Attribute))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    members.add(item.name)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    members.add(item.target.id)
                elif isinstance(item, ast.Assign):
                    members.update(t.id for t in item.targets if isinstance(t, ast.Name))
    return classes, module_names


def _has_member(class_name: str, member: str, seen: frozenset[str] = frozenset()) -> bool:
    classes, _ = _source_index()
    if class_name in seen or class_name not in classes:
        return False
    members, bases = classes[class_name]
    return member in members or any(
        _has_member(base, member, seen | {class_name}) for base in bases
    )


def test_every_cited_class_exists_in_source():
    classes, _ = _source_index()
    cited = set(
        re.findall(r"`([A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*)(?:\.\w+)?(?:\(\))?`", _guide_text())
    )
    cited -= NON_CODE_NAMES
    assert {"SchedulerPort", "SlurmSchedulerStrategy", "FieldMappingRegistry"} <= cited
    assert not [name for name in sorted(cited) if name not in classes]


def test_every_cited_class_member_exists():
    pairs = set(
        re.findall(r"`([A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*)\.(\w+)(?:\(\))?`", _guide_text())
    )
    assert ("ConfigurationManager", "override_scheduler_strategy") in pairs
    assert not [f"{c}.{m}" for c, m in sorted(pairs) if not _has_member(c, m)]


def test_every_cited_constant_exists_in_source():
    _, module_names = _source_index()
    constants = set(re.findall(r"`([A-Z][A-Z0-9_]{3,})`", _guide_text()))
    assert "BUILT_IN_FORMATS" in constants
    assert not [name for name in sorted(constants) if name not in module_names]

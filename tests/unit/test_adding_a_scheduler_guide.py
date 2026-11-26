"""The scheduler authoring guide must reference code that exists."""

import importlib
import re
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


def test_dotted_orb_paths_resolve():
    for dotted in sorted(set(re.findall(r"`(orb(?:\.\w+)+)`", _guide_text()))):
        module_path, _, attr = dotted.rpartition(".")
        try:
            module = importlib.import_module(dotted)
        except ImportError:
            module = importlib.import_module(module_path)
            assert hasattr(module, attr), dotted


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

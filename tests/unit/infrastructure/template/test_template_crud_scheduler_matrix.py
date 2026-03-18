"""Template CRUD tests across scheduler × file location combinations.

Tests the full create→get→delete→create cycle for every meaningful combination
of scheduler strategy and template file location. These are the regression tests
for the two bugs fixed in delete_template:
  1. Only searching path[0] instead of all paths
  2. Stale cache after delete causing false DuplicateError on re-create

Approach: real files in tmp_path, direct construction of the stack.
No mocking of open/json.dump — the bugs lived in the file I/O path-search logic.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from orb.config.managers.configuration_manager import ConfigurationManager
from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.dtos import TemplateDTO
from orb.infrastructure.template.services.template_storage_service import TemplateStorageService
from orb.infrastructure.template.template_cache_service import create_template_cache_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> LoggingPort:
    logger = Mock(spec=LoggingPort)
    logger.debug = Mock()
    logger.info = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    return logger


def _write_template_file(path: Path, templates: list[dict[str, Any]], scheduler_type: str) -> None:
    """Write a templates JSON file in the envelope format the storage service expects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"scheduler_type": scheduler_type, "templates": templates}),
        encoding="utf-8",
    )


def _make_stack(scheduler: str) -> TemplateConfigurationManager:
    """Construct a real TemplateConfigurationManager with real file I/O."""
    logger = _make_logger()
    config_manager = ConfigurationManager(config_dict={})

    if scheduler == "hostfactory":
        strategy = HostFactorySchedulerStrategy(config_port=config_manager, logger=logger)
    else:
        strategy = DefaultSchedulerStrategy(config_port=config_manager, logger=logger)

    cache_service = create_template_cache_service("noop", logger)
    storage_service = TemplateStorageService(scheduler_strategy=strategy, logger=logger)

    return TemplateConfigurationManager(
        config_manager=config_manager,
        scheduler_strategy=strategy,
        logger=logger,
        cache_service=cache_service,
        storage_service=storage_service,
    )


# Minimal template dicts written to disk (snake_case — storage service reads raw dicts)
_DEFAULT_TEMPLATE_DICT: dict[str, Any] = {
    "template_id": "t1",
    "name": "Test",
    "provider_type": "aws",
}

_HF_TEMPLATE_DICT: dict[str, Any] = {
    "templateId": "t1",
    "name": "Test",
    "providerType": "aws",
}

_TEMPLATE_DTO = TemplateDTO(template_id="t1", name="Test")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "config"
    d.mkdir()
    monkeypatch.setenv("ORB_CONFIG_DIR", str(d))
    return d


# ---------------------------------------------------------------------------
# Case A: default scheduler, template in aws_templates.json (path[0])
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_default_scheduler_aws_templates_json(config_dir: Path) -> None:
    """Case A: happy path — file is path[0] for default scheduler."""
    _write_template_file(
        config_dir / "aws_templates.json",
        [_DEFAULT_TEMPLATE_DICT],
        "default",
    )

    manager = _make_stack("default")

    assert await manager.get_template_by_id("t1") is not None

    await manager.delete_template("t1")
    assert await manager.get_template_by_id("t1") is None

    # Re-create must not raise
    await manager.save_template(_TEMPLATE_DTO)
    assert await manager.get_template_by_id("t1") is not None


# ---------------------------------------------------------------------------
# Case B: default scheduler, template in templates.json (path[3])
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_default_scheduler_templates_json(config_dir: Path) -> None:
    """Case B: template in templates.json (path[3] for default); after delete+create
    the template lives in aws_templates.json (path[0])."""
    _write_template_file(
        config_dir / "templates.json",
        [_DEFAULT_TEMPLATE_DICT],
        "default",
    )

    manager = _make_stack("default")

    assert await manager.get_template_by_id("t1") is not None

    await manager.delete_template("t1")
    assert await manager.get_template_by_id("t1") is None

    await manager.save_template(_TEMPLATE_DTO)
    assert await manager.get_template_by_id("t1") is not None

    # After save, template should now live in path[0] = aws_templates.json
    primary = config_dir / "aws_templates.json"
    assert primary.exists()
    data = json.loads(primary.read_text())
    ids = [t.get("template_id") or t.get("templateId") for t in data.get("templates", [])]
    assert "t1" in ids


# ---------------------------------------------------------------------------
# Case C: hostfactory scheduler, template in awsprov_templates.json (path[0] for HF
#         when provider_name resolves to "awsprov" — but without a registry,
#         provider_name="default", so path[0]="default_templates.json".
#         We use the actual path[0] the strategy produces.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_hf_scheduler_primary_path(config_dir: Path) -> None:
    """Case C: happy path — template in HF path[0] (default_templates.json when no registry)."""
    # Determine what path[0] actually is for HF with no registry
    logger = _make_logger()
    config_manager = ConfigurationManager(config_dict={})
    strategy = HostFactorySchedulerStrategy(config_port=config_manager, logger=logger)
    paths = strategy.get_template_paths()
    primary_filename = Path(paths[0]).name

    _write_template_file(
        config_dir / primary_filename,
        [_HF_TEMPLATE_DICT],
        "hostfactory",
    )

    manager = _make_stack("hostfactory")

    assert await manager.get_template_by_id("t1") is not None

    await manager.delete_template("t1")
    assert await manager.get_template_by_id("t1") is None

    await manager.save_template(_TEMPLATE_DTO)
    assert await manager.get_template_by_id("t1") is not None


# ---------------------------------------------------------------------------
# Case D: hostfactory scheduler, template in aws_templates.json (path[1] for HF)
#         This is the regression case — the bug only searched path[0].
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_hf_scheduler_aws_templates_json(config_dir: Path) -> None:
    """Case D (regression): template in aws_templates.json which is path[1] for HF.

    Before the fix, delete_template only searched path[0] and raised EntityNotFoundError.
    After delete, save_template must not raise DuplicateError (stale cache bug).
    """
    _write_template_file(
        config_dir / "aws_templates.json",
        [_HF_TEMPLATE_DICT],
        "hostfactory",
    )

    manager = _make_stack("hostfactory")

    # Must find the template even though it's not in path[0]
    assert await manager.get_template_by_id("t1") is not None

    # Must not raise EntityNotFoundError (was the first bug)
    await manager.delete_template("t1")

    # Must return None after delete (cache must be invalidated)
    assert await manager.get_template_by_id("t1") is None

    # Must not raise DuplicateError (was the second bug — stale cache)
    await manager.save_template(_TEMPLATE_DTO)
    assert await manager.get_template_by_id("t1") is not None


# ---------------------------------------------------------------------------
# Case E: hostfactory scheduler, template in templates.json (path[3] for HF)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_hf_scheduler_templates_json(config_dir: Path) -> None:
    """Case E: template in templates.json (path[3] for HF)."""
    _write_template_file(
        config_dir / "templates.json",
        [_HF_TEMPLATE_DICT],
        "hostfactory",
    )

    manager = _make_stack("hostfactory")

    assert await manager.get_template_by_id("t1") is not None

    await manager.delete_template("t1")
    assert await manager.get_template_by_id("t1") is None

    await manager.save_template(_TEMPLATE_DTO)
    assert await manager.get_template_by_id("t1") is not None


# ---------------------------------------------------------------------------
# Case F: HF-written template invisible to default scheduler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hf_template_invisible_to_default_scheduler(config_dir: Path) -> None:
    """Case F: template in HF path[0] is invisible to default scheduler.

    The default scheduler's path list does not include default_templates.json
    (the HF primary file when no registry is configured), so get returns None
    and save succeeds without a DuplicateError.
    """
    # Write to HF's path[0]
    logger = _make_logger()
    config_manager = ConfigurationManager(config_dict={})
    hf_strategy = HostFactorySchedulerStrategy(config_port=config_manager, logger=logger)
    hf_paths = hf_strategy.get_template_paths()
    hf_primary_filename = Path(hf_paths[0]).name

    # Only write to HF primary — do NOT write to aws_templates.json or templates.json
    _write_template_file(
        config_dir / hf_primary_filename,
        [_HF_TEMPLATE_DICT],
        "hostfactory",
    )

    # Construct default scheduler stack
    default_manager = _make_stack("default")

    # Default scheduler must not see the HF template
    result = await default_manager.get_template_by_id("t1")
    assert result is None, (
        f"Default scheduler should not see template in {hf_primary_filename}, but got: {result}"
    )

    # Save must succeed (no duplicate)
    await default_manager.save_template(_TEMPLATE_DTO)
    assert await default_manager.get_template_by_id("t1") is not None

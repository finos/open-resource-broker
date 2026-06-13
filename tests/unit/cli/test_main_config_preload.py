"""Tests for CLI explicit configuration preloading."""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from orb.cli.main import _preload_explicit_config
from orb.config.managers.configuration_manager import ConfigurationManager


def test_preload_explicit_config_registers_config_manager(monkeypatch, tmp_path):
    container = MagicMock()
    monkeypatch.setattr(
        "orb.infrastructure.di.container.get_container",
        lambda: container,
    )
    for key in ("ORB_CONFIG_DIR", "ORB_WORK_DIR", "ORB_LOG_DIR", "ORB_SCRIPTS_DIR"):
        monkeypatch.delenv(key, raising=False)

    logger = MagicMock()
    config_file = tmp_path / "config" / "config.json"
    config_file.parent.mkdir()
    config_file.write_text("{}", encoding="utf-8")

    _preload_explicit_config(SimpleNamespace(config=str(config_file)), logger)

    container.register_instance.assert_called_once()
    registered_type, registered_cm = container.register_instance.call_args.args
    assert registered_type is ConfigurationManager
    assert registered_cm._config_file == str(config_file)
    assert registered_cm._config_dict is None
    assert os.environ["ORB_CONFIG_DIR"] == str(tmp_path / "config")
    assert os.environ["ORB_WORK_DIR"] == str(tmp_path / "work")
    assert os.environ["ORB_LOG_DIR"] == str(tmp_path / "logs")
    assert os.environ["ORB_SCRIPTS_DIR"] == str(tmp_path / "scripts")
    logger.warning.assert_not_called()


def test_preload_explicit_config_honors_runtime_dirs_from_config(monkeypatch, tmp_path):
    container = MagicMock()
    monkeypatch.setattr(
        "orb.infrastructure.di.container.get_container",
        lambda: container,
    )
    for key in ("ORB_CONFIG_DIR", "ORB_WORK_DIR", "ORB_LOG_DIR", "ORB_SCRIPTS_DIR"):
        monkeypatch.delenv(key, raising=False)

    config_file = tmp_path / "custom-config" / "config.json"
    config_file.parent.mkdir()
    work_dir = tmp_path / "custom-work"
    logs_dir = tmp_path / "custom-logs"
    scripts_dir = tmp_path / "custom-scripts"
    config_file.write_text(
        json.dumps(
            {
                "logging": {"file_path": str(logs_dir / "orb.log")},
                "storage": {"json_strategy": {"base_path": str(work_dir)}},
                "scripts_dir": str(scripts_dir),
            }
        ),
        encoding="utf-8",
    )

    _preload_explicit_config(SimpleNamespace(config=str(config_file)), MagicMock())

    assert os.environ["ORB_CONFIG_DIR"] == str(config_file.parent)
    assert os.environ["ORB_WORK_DIR"] == str(work_dir)
    assert os.environ["ORB_LOG_DIR"] == str(logs_dir)
    assert os.environ["ORB_SCRIPTS_DIR"] == str(scripts_dir)


def test_preload_explicit_config_skips_when_no_config(monkeypatch):
    container = MagicMock()
    monkeypatch.setattr(
        "orb.infrastructure.di.container.get_container",
        lambda: container,
    )

    _preload_explicit_config(SimpleNamespace(config=None), MagicMock())

    container.register_instance.assert_not_called()

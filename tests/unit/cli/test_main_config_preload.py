"""Tests for CLI explicit configuration preloading."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from orb.cli.main import _preload_explicit_config
from orb.config.managers.configuration_manager import ConfigurationManager


def test_preload_explicit_config_registers_config_manager(monkeypatch):
    container = MagicMock()
    monkeypatch.setattr(
        "orb.infrastructure.di.container.get_container",
        lambda: container,
    )
    logger = MagicMock()

    _preload_explicit_config(SimpleNamespace(config="config/oci_config.json"), logger)

    container.register_instance.assert_called_once()
    registered_type, registered_cm = container.register_instance.call_args.args
    assert registered_type is ConfigurationManager
    assert registered_cm._config_file == "config/oci_config.json"
    assert registered_cm._config_dict is None
    logger.warning.assert_not_called()


def test_preload_explicit_config_skips_when_no_config(monkeypatch):
    container = MagicMock()
    monkeypatch.setattr(
        "orb.infrastructure.di.container.get_container",
        lambda: container,
    )

    _preload_explicit_config(SimpleNamespace(config=None), MagicMock())

    container.register_instance.assert_not_called()

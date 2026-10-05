"""Unit tests for StorageStrategyFactory."""

from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.storage.factory import StorageStrategyFactory

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    with patch("orb.infrastructure.storage.factory.get_storage_registry", return_value=registry):
        yield registry


class TestGetStorageType:
    def test_reads_strategy_from_object_with_storage_attribute(self):
        factory = StorageStrategyFactory()
        config = MagicMock()
        config.storage.strategy = "sql"

        assert factory._get_storage_type(config) == "sql"

    def test_reads_strategy_from_dict(self):
        factory = StorageStrategyFactory()
        config = {"storage": {"strategy": "json"}}

        assert factory._get_storage_type(config) == "json"

    def test_falls_back_to_config_manager(self):
        config_manager = MagicMock()
        config_manager.get_storage_strategy.return_value = "sql"
        factory = StorageStrategyFactory(config_manager=config_manager)

        assert factory._get_storage_type({}) == "sql"
        config_manager.get_storage_strategy.assert_called_once_with()

    def test_defaults_to_json_without_config_manager(self):
        factory = StorageStrategyFactory()

        assert factory._get_storage_type({}) == "json"

    def test_dict_without_strategy_falls_through(self):
        config_manager = MagicMock()
        config_manager.get_storage_strategy.return_value = "sql"
        factory = StorageStrategyFactory(config_manager=config_manager)

        assert factory._get_storage_type({"storage": {}}) == "sql"

    def test_dict_with_non_dict_storage_falls_through(self):
        config_manager = MagicMock()
        config_manager.get_storage_strategy.return_value = "json"
        factory = StorageStrategyFactory(config_manager=config_manager)

        assert factory._get_storage_type({"storage": "not-a-dict"}) == "json"


class TestCreateStrategy:
    def test_create_strategy_delegates_to_registry(self, mock_registry):
        factory = StorageStrategyFactory()
        mock_registry.create_strategy.return_value = "strategy-instance"

        result = factory.create_strategy("sql", {"a": 1})

        mock_registry.create_strategy.assert_called_once_with("sql", {"a": 1})
        assert result == "strategy-instance"


class TestCreateMachineStorageStrategy:
    def test_uses_provided_config(self, mock_registry):
        factory = StorageStrategyFactory()
        config = {"storage": {"strategy": "json"}}

        factory.create_machine_storage_strategy(config)

        mock_registry.create_strategy.assert_called_once_with("json", config)

    def test_derives_config_from_config_manager_when_none(self, mock_registry):
        config_manager = MagicMock()
        config_manager.app_config.model_dump.return_value = {"storage": {"strategy": "sql"}}
        factory = StorageStrategyFactory(config_manager=config_manager)

        factory.create_machine_storage_strategy()

        config_manager.app_config.model_dump.assert_called_once_with()
        mock_registry.create_strategy.assert_called_once_with(
            "sql", {"storage": {"strategy": "sql"}}
        )


class TestCreateRequestStorageStrategy:
    def test_uses_provided_config(self, mock_registry):
        factory = StorageStrategyFactory()
        config = {"storage": {"strategy": "json"}}

        factory.create_request_storage_strategy(config)

        mock_registry.create_strategy.assert_called_once_with("json", config)

    def test_derives_config_from_config_manager_when_none(self, mock_registry):
        config_manager = MagicMock()
        config_manager.app_config.model_dump.return_value = {"storage": {"strategy": "sql"}}
        factory = StorageStrategyFactory(config_manager=config_manager)

        factory.create_request_storage_strategy()

        mock_registry.create_strategy.assert_called_once_with(
            "sql", {"storage": {"strategy": "sql"}}
        )


class TestCreateTemplateStorageStrategy:
    def test_uses_provided_config(self, mock_registry):
        factory = StorageStrategyFactory()
        config = {"storage": {"strategy": "json"}}

        factory.create_template_storage_strategy(config)

        mock_registry.create_strategy.assert_called_once_with("json", config)

    def test_derives_config_from_config_manager_when_none(self, mock_registry):
        config_manager = MagicMock()
        config_manager.app_config.model_dump.return_value = {"storage": {"strategy": "sql"}}
        factory = StorageStrategyFactory(config_manager=config_manager)

        factory.create_template_storage_strategy()

        mock_registry.create_strategy.assert_called_once_with(
            "sql", {"storage": {"strategy": "sql"}}
        )


class TestClearCache:
    def test_is_a_noop(self):
        factory = StorageStrategyFactory()
        assert factory.clear_cache() is None

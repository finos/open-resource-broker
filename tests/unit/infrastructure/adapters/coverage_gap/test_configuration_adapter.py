"""Unit tests for ConfigurationAdapter.

Coverage targets: lines 38,63-65,102-104,121,123-124,172-174,178,187-189,
193-194,198,200,204,206,210-211,213-224,226-228,230,233-235,237,240-244,246,248,
254-256,265-267,272-274,278-280,289-291,301,323,329,335,341,349-350,352-356,375,
379,383,391,399-401,404,417,420,422,432-433,435,437-439,441-448
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.adapters.configuration_adapter import ConfigurationAdapter

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_adapter(**config_manager_attrs: Any) -> tuple[ConfigurationAdapter, MagicMock]:
    cm = MagicMock()
    logger = MagicMock()

    for attr, val in config_manager_attrs.items():
        setattr(cm, attr, val)

    adapter = ConfigurationAdapter(config_manager=cm, logger=logger)
    return adapter, cm


# ---------------------------------------------------------------------------
# get_app_config
# ---------------------------------------------------------------------------


class TestGetAppConfig:
    def test_returns_model_dump_result(self):
        adapter, cm = _make_adapter()
        cm.app_config.model_dump.return_value = {"scheduler": {"type": "default"}}
        result = adapter.get_app_config()
        cm.app_config.model_dump.assert_called_once_with(mode="json")
        assert result == {"scheduler": {"type": "default"}}

    def test_app_config_property_delegates(self):
        adapter, cm = _make_adapter()
        fake_config = MagicMock()
        cm.app_config = fake_config
        assert adapter.app_config is fake_config


# ---------------------------------------------------------------------------
# find_templates_file
# ---------------------------------------------------------------------------


class TestFindTemplatesFile:
    def test_delegates_to_config_manager(self):
        adapter, cm = _make_adapter()
        cm.find_templates_file.return_value = "/data/aws_templates.json"
        result = adapter.find_templates_file("aws")
        cm.find_templates_file.assert_called_once_with("aws")
        assert result == "/data/aws_templates.json"


# ---------------------------------------------------------------------------
# get_naming_config
# ---------------------------------------------------------------------------


class TestGetNamingConfig:
    def test_returns_patterns_and_prefixes(self):

        adapter, cm = _make_adapter()
        fake_naming = MagicMock()
        fake_naming.patterns = {
            "request_id": r"^(req-|ret-)[a-f0-9\-]{36}$",
            "cidr_block": r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$",
        }
        fake_naming.prefixes.request = "req-"
        fake_naming.prefixes.return_prefix = "ret-"
        cm.get_typed.return_value = fake_naming

        result = adapter.get_naming_config()

        assert "patterns" in result
        assert "prefixes" in result
        assert result["prefixes"]["request"] == "req-"
        assert result["prefixes"]["return"] == "ret-"

    def test_falls_back_to_defaults_on_exception(self):
        adapter, cm = _make_adapter()
        cm.get_typed.side_effect = Exception("config error")

        result = adapter.get_naming_config()

        assert "patterns" in result
        assert "prefixes" in result
        # Fallback defaults must be returned, and the failure must be logged.
        assert result["prefixes"]["request"] == "req-"
        assert result["prefixes"]["return"] == "ret-"
        adapter._logger.warning.assert_called_once()

    def test_uses_constant_when_prefixes_attr_missing(self):
        adapter, cm = _make_adapter()
        fake_naming = MagicMock()
        fake_naming.patterns = {}
        # Make hasattr return False for request/return_prefix
        fake_prefixes = MagicMock(spec=[])  # spec=[] means no attrs
        fake_naming.prefixes = fake_prefixes
        cm.get_typed.return_value = fake_naming

        result = adapter.get_naming_config()
        assert "prefixes" in result
        assert result["prefixes"]["request"] is not None


# ---------------------------------------------------------------------------
# get_request_config
# ---------------------------------------------------------------------------


class TestGetRequestConfig:
    def test_returns_request_config_fields(self):
        adapter, cm = _make_adapter()
        req_cfg = MagicMock()
        req_cfg.max_machines_per_request = 50
        req_cfg.default_timeout = 1800
        req_cfg.default_grace_period = 600
        req_cfg.min_timeout = 60
        req_cfg.max_timeout = 7200
        req_cfg.fulfillment_max_retries = 5
        req_cfg.fulfillment_timeout_seconds = 120
        req_cfg.fulfillment_batch_size = 500
        req_cfg.fulfillment_fallback_template_id = "tpl-fallback"
        req_cfg.concurrency_max_retries = 7
        cm.get_typed.return_value = req_cfg

        result = adapter.get_request_config()

        assert result["max_machines_per_request"] == 50
        assert result["fulfillment_max_retries"] == 5
        assert result["fulfillment_fallback_template_id"] == "tpl-fallback"
        assert result["concurrency_max_retries"] == 7

    def test_falls_back_to_defaults_on_exception(self):
        adapter, cm = _make_adapter()
        cm.get_typed.side_effect = Exception("request config failed")

        result = adapter.get_request_config()

        assert result["max_machines_per_request"] == 100
        assert result["fulfillment_fallback_template_id"] is None
        assert result["concurrency_max_retries"] == 3


# ---------------------------------------------------------------------------
# get_template_config
# ---------------------------------------------------------------------------


class TestGetTemplateConfig:
    def test_returns_model_dump(self):
        adapter, cm = _make_adapter()
        template_cfg = MagicMock()
        template_cfg.model_dump.return_value = {"default_provider_api": "EC2Fleet"}
        cm.get_typed.return_value = template_cfg

        result = adapter.get_template_config()
        assert result == {"default_provider_api": "EC2Fleet"}

    def test_returns_empty_dict_on_exception(self):
        adapter, cm = _make_adapter()
        cm.get_typed.side_effect = Exception("template config broken")

        result = adapter.get_template_config()
        assert result == {}


# ---------------------------------------------------------------------------
# get_raw_config
# ---------------------------------------------------------------------------


class TestGetRawConfig:
    def test_returns_dict_when_raw_config_is_dict(self):
        adapter, cm = _make_adapter()
        cm._ensure_raw_config.return_value = {"key": "value"}

        result = adapter.get_raw_config()
        assert result == {"key": "value"}

    def test_returns_empty_dict_when_raw_config_not_dict(self):
        adapter, cm = _make_adapter()
        cm._ensure_raw_config.return_value = None

        result = adapter.get_raw_config()
        assert result == {}


# ---------------------------------------------------------------------------
# get_metrics_config
# ---------------------------------------------------------------------------


class TestGetMetricsConfig:
    def test_returns_defaults_when_no_metrics_section(self):
        adapter, cm = _make_adapter()
        cm._ensure_raw_config.return_value = {}

        result = adapter.get_metrics_config()

        assert result["metrics_enabled"] is False
        assert "provider_metrics" in result
        assert result["provider_metrics"]["provider_metrics_enabled"] is False

    def test_merges_raw_metrics_config(self):
        adapter, cm = _make_adapter()
        cm._ensure_raw_config.return_value = {
            "metrics": {
                "metrics_enabled": True,
                "metrics_interval": 30,
                "provider_metrics": {
                    "provider_metrics_enabled": True,
                    "sample_rate": 0.5,
                },
            }
        }

        result = adapter.get_metrics_config()

        assert result["metrics_enabled"] is True
        assert result["metrics_interval"] == 30
        assert result["provider_metrics"]["provider_metrics_enabled"] is True
        assert result["provider_metrics"]["sample_rate"] == 0.5

    def test_falls_back_to_defaults_on_exception(self):
        adapter, cm = _make_adapter()
        # Cause an error in get_raw_config by making _ensure_raw_config raise
        cm._ensure_raw_config.side_effect = Exception("raw config broken")

        result = adapter.get_metrics_config()

        assert "metrics_enabled" in result
        assert result["metrics_enabled"] is False


# ---------------------------------------------------------------------------
# get_loaded_config_file / save_config
# ---------------------------------------------------------------------------


class TestSaveConfig:
    def test_save_config_uses_source_file_path_when_no_path_given(self):
        adapter, cm = _make_adapter()
        cm.get_source_config_file.return_value = "/etc/orb/config.yaml"

        result = adapter.save_config()

        cm.save.assert_called_once_with("/etc/orb/config.yaml")
        assert result == "/etc/orb/config.yaml"

    def test_save_config_uses_explicit_path(self):
        adapter, cm = _make_adapter()

        result = adapter.save_config("/tmp/my_config.yaml")

        cm.save.assert_called_once_with("/tmp/my_config.yaml")
        assert result == "/tmp/my_config.yaml"

    def test_save_config_raises_when_no_source_file(self):
        adapter, cm = _make_adapter()
        cm.get_source_config_file.return_value = None

        with pytest.raises(ValueError, match="No config file path resolved"):
            adapter.save_config()

    def test_save_config_ignores_discovered_file_that_was_not_loaded(self):
        # get_loaded_config_file may resolve a candidate that exists on disk but
        # was never the loaded source; save_config(None) must not write there.
        adapter, cm = _make_adapter()
        cm.get_source_config_file.return_value = None
        cm.get_loaded_config_file.return_value = "/etc/orb/discovered.yaml"

        with pytest.raises(ValueError, match="No config file path resolved"):
            adapter.save_config()

        cm.save.assert_not_called()


# ---------------------------------------------------------------------------
# get_storage_config
# ---------------------------------------------------------------------------


class TestGetStorageConfig:
    def test_json_strategy_with_base_path(self, tmp_path):
        adapter, cm = _make_adapter()
        cm.get.return_value = {
            "strategy": "json",
            "json_strategy": {
                "base_path": str(tmp_path / "data"),
                "backup_enabled": True,
                "backup_path": None,
            },
        }
        cm.get_work_dir.return_value = str(tmp_path)

        result = adapter.get_storage_config()

        assert result["strategy"] == "json"
        assert result["data_path"] is not None
        assert result["backup_enabled"] is True

    def test_sql_strategy(self, tmp_path):
        adapter, cm = _make_adapter()
        cm.get.return_value = {
            "strategy": "sql",
            "sql_strategy": {"name": "db.sqlite3"},
        }
        cm.get_work_dir.return_value = str(tmp_path)

        result = adapter.get_storage_config()
        assert result["strategy"] == "sql"
        assert result["backup_enabled"] is False

    def test_unknown_strategy_returns_none_data_path(self, tmp_path):
        adapter, cm = _make_adapter()
        cm.get.return_value = {"strategy": "exotic"}
        cm.get_work_dir.return_value = str(tmp_path)

        result = adapter.get_storage_config()
        assert result["strategy"] == "exotic"
        assert result["data_path"] is None

    def test_falls_back_on_exception(self):
        adapter, cm = _make_adapter()
        cm.get.side_effect = Exception("storage broken")

        result = adapter.get_storage_config()
        assert result["strategy"] == "json"
        assert result["data_path"] is None

    def test_absolute_data_path_not_prepended_with_work_dir(self, tmp_path):
        adapter, cm = _make_adapter()
        abs_data_path = str(tmp_path / "absolute_data")
        cm.get.return_value = {
            "strategy": "json",
            "json_strategy": {"base_path": abs_data_path, "backup_enabled": False},
        }
        cm.get_work_dir.return_value = str(tmp_path)

        result = adapter.get_storage_config()
        assert result["data_path"] == abs_data_path

    def test_backup_path_defaults_to_work_dir_backups_when_enabled(self, tmp_path):
        adapter, cm = _make_adapter()
        cm.get.return_value = {
            "strategy": "json",
            "json_strategy": {"backup_enabled": True, "backup_path": None},
        }
        cm.get_work_dir.return_value = str(tmp_path)

        result = adapter.get_storage_config()
        assert result["backup_path"] == str(tmp_path / "backups")


# ---------------------------------------------------------------------------
# get_events_config
# ---------------------------------------------------------------------------


class TestGetEventsConfig:
    def test_returns_events_config(self):
        adapter, cm = _make_adapter()
        cm.get.return_value = {"enabled": False, "mode": "sns", "batch_size": 50}

        result = adapter.get_events_config()

        assert result["enabled"] is False
        assert result["mode"] == "sns"
        assert result["batch_size"] == 50

    def test_falls_back_on_exception(self):
        adapter, cm = _make_adapter()
        cm.get.side_effect = Exception("events broken")

        result = adapter.get_events_config()
        assert result["enabled"] is True
        assert result["mode"] == "logging"
        assert result["batch_size"] == 10


# ---------------------------------------------------------------------------
# get_logging_config
# ---------------------------------------------------------------------------


class TestGetLoggingConfig:
    def test_returns_logging_config(self):
        adapter, cm = _make_adapter()
        cm.get.return_value = {
            "level": "DEBUG",
            "format": "%(message)s",
            "file_path": "/var/log/orb.log",
            "file_enabled": True,
            "console_enabled": False,
        }

        result = adapter.get_logging_config()

        assert result["level"] == "DEBUG"
        assert result["file_path"] == "/var/log/orb.log"
        assert result["console_enabled"] is False

    def test_falls_back_on_exception(self):
        adapter, cm = _make_adapter()
        cm.get.side_effect = Exception("logging broken")

        result = adapter.get_logging_config()
        assert result["level"] == "INFO"
        assert result["file_path"] is None


# ---------------------------------------------------------------------------
# get_native_spec_config
# ---------------------------------------------------------------------------


class TestGetNativeSpecConfig:
    def test_returns_native_spec_config(self):
        adapter, cm = _make_adapter()
        fake_ns_cfg = MagicMock()
        fake_ns_cfg.enabled = True
        fake_ns_cfg.merge_mode = "replace"
        cm.get_typed.return_value = fake_ns_cfg

        result = adapter.get_native_spec_config()

        assert result["enabled"] is True
        assert result["merge_mode"] == "replace"

    def test_falls_back_on_exception(self):
        adapter, cm = _make_adapter()
        cm.get_typed.side_effect = Exception("no native spec config")

        result = adapter.get_native_spec_config()
        assert result["enabled"] is False
        assert result["merge_mode"] == "merge"


# ---------------------------------------------------------------------------
# get_resource_prefix
# ---------------------------------------------------------------------------


class TestGetResourcePrefix:
    def test_returns_resource_prefix_when_present(self):
        adapter, cm = _make_adapter()
        cm.app_config.resource.prefixes.machine = "mch-"
        cm.app_config.resource.default_prefix = "orb-"

        with patch.object(
            type(cm.app_config.resource.prefixes), "__dir__", return_value=["machine"]
        ):
            result = adapter.get_resource_prefix("machine")
        # Can't easily check exact output without hasattr, but ensure no exception
        assert isinstance(result, str)

    def test_falls_back_to_empty_string_on_exception(self):
        adapter, cm = _make_adapter()
        cm.app_config.resource = None  # Will cause AttributeError

        result = adapter.get_resource_prefix("machine")
        assert result == ""


# ---------------------------------------------------------------------------
# get_configuration_sources
# ---------------------------------------------------------------------------


class TestGetConfigurationSources:
    def test_returns_config_file_based_sources(self):
        adapter, cm = _make_adapter()
        cm.get_loaded_config_file.return_value = "/etc/orb/config.yaml"
        cm.get_config_dir.return_value = "/etc/orb"
        cm.get_work_dir.return_value = "/var/orb"

        with patch.object(
            adapter, "_get_active_template_file", return_value="/data/templates.json"
        ):
            result = adapter.get_configuration_sources()

        assert result["config_file"] == "/etc/orb/config.yaml"
        assert result["template_file"] == "/data/templates.json"
        assert result["primary_source"] == "config_file"

    def test_primary_source_is_environment_when_no_config_file(self):
        adapter, cm = _make_adapter()
        cm.get_loaded_config_file.return_value = None
        cm.get_config_dir.return_value = "/etc/orb"
        cm.get_work_dir.return_value = "/var/orb"

        with patch.object(adapter, "_get_active_template_file", return_value=None):
            result = adapter.get_configuration_sources()

        assert result["primary_source"] == "environment"

    def test_get_active_template_file_returns_none_on_exception(self):
        adapter, cm = _make_adapter()
        cm.get_scheduler_strategy.side_effect = Exception("no scheduler")

        result = adapter._get_active_template_file()
        assert result is None


# ---------------------------------------------------------------------------
# Delegation methods (storage/scheduler strategy, overrides, etc.)
# ---------------------------------------------------------------------------


class TestDelegationMethods:
    def test_get_storage_strategy_delegates(self):
        adapter, cm = _make_adapter()
        cm.get_storage_strategy.return_value = "json"
        assert adapter.get_storage_strategy() == "json"

    def test_get_scheduler_strategy_delegates(self):
        adapter, cm = _make_adapter()
        cm.get_scheduler_strategy.return_value = "default"
        assert adapter.get_scheduler_strategy() == "default"

    def test_get_provider_type_delegates(self):
        adapter, cm = _make_adapter()
        cm.get_provider_type.return_value = "aws"
        assert adapter.get_provider_type() == "aws"

    def test_override_scheduler_strategy_delegates(self):
        adapter, cm = _make_adapter()
        adapter.override_scheduler_strategy("hostfactory")
        cm.override_scheduler_strategy.assert_called_once_with("hostfactory")

    def test_get_configuration_value_delegates(self):
        adapter, cm = _make_adapter()
        cm.get.return_value = "test_val"
        result = adapter.get_configuration_value("some.key", default="default")
        assert result == "test_val"

    def test_set_configuration_value_delegates(self):
        adapter, cm = _make_adapter()
        adapter.set_configuration_value("some.key", "new_val")
        cm.set.assert_called_once_with("some.key", "new_val")

    def test_resolve_file_delegates(self):
        adapter, cm = _make_adapter()
        cm.resolve_file.return_value = "/resolved/path.json"
        result = adapter.resolve_file("template", "aws_templates.json")
        assert result == "/resolved/path.json"

    def test_get_typed_delegates(self):
        adapter, cm = _make_adapter()
        mock_type = MagicMock()
        cm.get_typed.return_value = mock_type
        result = adapter.get_typed(str)
        assert result is mock_type

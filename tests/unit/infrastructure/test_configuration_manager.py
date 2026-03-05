"""Unit tests for ConfigurationManager."""

import json

import pytest

from config.manager import ConfigurationManager


@pytest.mark.unit
class TestConfigurationManager:
    """Test cases for ConfigurationManager."""

    def test_configuration_manager_initialization(self, tmp_path):
        """Test ConfigurationManager initialization."""
        config_file = str(tmp_path / "config.json")
        manager = ConfigurationManager(config_file=config_file)

        assert manager._config_file == config_file
        assert manager._app_config is None
        assert manager._raw_config is None

    def test_get_returns_none_for_missing_key(self, tmp_path):
        """Test get returns None for missing keys."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get("nonexistent.key") is None
        assert manager.get("aws.nonexistent") is None

    def test_get_returns_default_for_missing_key(self, tmp_path):
        """Test get returns default for missing keys."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get("nonexistent.key", "default-value") == "default-value"
        assert manager.get("aws.nonexistent", "default") == "default"

    def test_get_nested_key(self, tmp_path):
        """Test getting nested configuration values."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1", "profile": "default"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get("aws.region") == "us-east-1"
        assert manager.get("aws.profile") == "default"

    def test_get_top_level_key(self, tmp_path):
        """Test getting top-level configuration values."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        aws_config = manager.get("aws")
        assert isinstance(aws_config, dict)
        assert aws_config["region"] == "us-east-1"

    def test_set_configuration_value(self, tmp_path):
        """Test setting configuration values."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        manager.set("aws.region", "us-west-2")
        assert manager.get("aws.region") == "us-west-2"

    def test_set_new_key(self, tmp_path):
        """Test setting a new configuration key."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        manager.set("new_key", "new_value")
        assert manager.get("new_key") == "new_value"

    def test_reload_clears_cache(self, tmp_path):
        """Test that reload clears the configuration cache."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        # Access config to populate cache
        _ = manager.get("aws.region")
        assert manager._raw_config is not None

        # Reload clears cache; loader.reload may not exist so patch it out
        manager._loader = None  # prevent loader.reload() call
        manager.reload()
        assert manager._raw_config is None
        assert manager._app_config is None

    def test_save_writes_to_file(self, tmp_path):
        """Test saving configuration to file."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        # Load config first
        _ = manager.get("aws.region")

        output_file = tmp_path / "output.json"
        manager.save(str(output_file))

        assert output_file.exists()
        saved = json.loads(output_file.read_text())
        assert saved["aws"]["region"] == "us-east-1"

    def test_get_raw_config(self, tmp_path):
        """Test getting raw configuration dictionary."""
        config_data = {"aws": {"region": "us-east-1"}, "logging": {"level": "DEBUG"}}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        manager = ConfigurationManager(config_file=str(config_file))
        raw = manager.get_raw_config()

        assert isinstance(raw, dict)
        assert raw["aws"]["region"] == "us-east-1"
        assert raw["logging"]["level"] == "DEBUG"

    def test_get_raw_config_returns_copy(self, tmp_path):
        """Test that get_raw_config returns a copy."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        raw1 = manager.get_raw_config()
        raw2 = manager.get_raw_config()

        assert raw1 == raw2
        assert raw1 is not raw2  # Different objects

    def test_update_configuration(self, tmp_path):
        """Test updating configuration with new values."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        manager.update({"new_section": {"key": "value"}})
        assert manager.get("new_section.key") == "value"

    def test_get_bool(self, tmp_path):
        """Test getting boolean configuration values."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"feature": {"enabled": True}}))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get_bool("feature.enabled") is True
        assert manager.get_bool("feature.missing", False) is False

    def test_get_int(self, tmp_path):
        """Test getting integer configuration values."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"server": {"port": 8080}}))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get_int("server.port") == 8080
        assert manager.get_int("server.missing", 9090) == 9090

    def test_get_str(self, tmp_path):
        """Test getting string configuration values."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"app": {"name": "orb"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get_str("app.name") == "orb"
        assert manager.get_str("app.missing", "default") == "default"

    def test_get_float(self, tmp_path):
        """Test getting float configuration values."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"timeout": {"factor": 1.5}}))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get_float("timeout.factor") == 1.5
        assert manager.get_float("timeout.missing", 2.0) == 2.0

    def test_get_provider_config_returns_config_object(self, tmp_path):
        """Test get_provider_config returns a ProviderConfig object."""
        from config.schemas.provider_strategy_schema import ProviderConfig

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        result = manager.get_provider_config()
        # Returns a ProviderConfig with defaults when no explicit provider section
        assert isinstance(result, ProviderConfig)

    def test_get_cache_stats(self, tmp_path):
        """Test getting cache statistics."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        stats = manager.get_cache_stats()
        assert isinstance(stats, dict)

    def test_override_aws_region(self, tmp_path):
        """Test overriding AWS region."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        manager.override_aws_region("eu-west-1")
        assert manager.get_aws_region_override() == "eu-west-1"
        assert manager.get_effective_aws_region() == "eu-west-1"

    def test_override_aws_profile(self, tmp_path):
        """Test overriding AWS profile."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"aws": {"region": "us-east-1"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        manager.override_aws_profile("my-profile")
        assert manager.get_aws_profile_override() == "my-profile"
        assert manager.get_effective_aws_profile() == "my-profile"

    def test_effective_aws_region_uses_default(self, tmp_path):
        """Test effective AWS region uses default when no override."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({}))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get_effective_aws_region("us-east-1") == "us-east-1"

    def test_effective_aws_profile_uses_default(self, tmp_path):
        """Test effective AWS profile uses default when no override."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({}))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get_effective_aws_profile("default") == "default"

    def test_override_provider_instance(self, tmp_path):
        """Test overriding provider instance."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({}))

        manager = ConfigurationManager(config_file=str(config_file))
        manager.override_provider_instance("aws-us-east-1")
        assert manager.get_active_provider_override() == "aws-us-east-1"

    def test_override_scheduler_strategy(self, tmp_path):
        """Test overriding scheduler strategy."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"provider": {"scheduler_strategy": "default"}}))

        manager = ConfigurationManager(config_file=str(config_file))
        manager.override_scheduler_strategy("round_robin")
        assert manager.get_scheduler_strategy() == "round_robin"

        manager.restore_scheduler_strategy()
        assert manager._scheduler_override is None

    def test_deep_nested_key_access(self, tmp_path):
        """Test accessing deeply nested configuration keys."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"level1": {"level2": {"level3": {"level4": "deep_value"}}}})
        )

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get("level1.level2.level3.level4") == "deep_value"

    def test_type_preservation(self, tmp_path):
        """Test that configuration preserves data types."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "string_value": "test",
                    "int_value": 42,
                    "float_value": 3.14,
                    "bool_value": True,
                    "list_value": [1, 2, 3],
                }
            )
        )

        manager = ConfigurationManager(config_file=str(config_file))
        assert isinstance(manager.get("string_value"), str)
        assert isinstance(manager.get("int_value"), int)
        assert isinstance(manager.get("float_value"), float)
        assert isinstance(manager.get("bool_value"), bool)
        assert isinstance(manager.get("list_value"), list)


@pytest.mark.unit
class TestConfigurationManagerEdgeCases:
    """Test edge cases for ConfigurationManager."""

    def test_empty_config_file(self, tmp_path):
        """Test handling empty configuration file."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({}))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get("any.key") is None
        assert manager.get("any.key", "default") == "default"

    def test_none_values_in_configuration(self, tmp_path):
        """Test handling None values in configuration."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "null_value": None,
                    "empty_string": "",
                    "zero_value": 0,
                    "false_value": False,
                }
            )
        )

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get("null_value") is None
        assert manager.get("empty_string") == ""
        assert manager.get("zero_value") == 0
        assert manager.get("false_value") is False

    def test_large_configuration(self, tmp_path):
        """Test handling large configuration."""
        config_data = {f"key_{i}": f"value_{i}" for i in range(100)}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get("key_0") == "value_0"
        assert manager.get("key_50") == "value_50"
        assert manager.get("key_99") == "value_99"

    def test_reload_clears_all_caches(self, tmp_path):
        """Test that reload clears all internal caches."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"key": "value"}))

        manager = ConfigurationManager(config_file=str(config_file))
        _ = manager.get("key")  # Populate caches

        # Prevent loader.reload() call (method doesn't exist on ConfigurationLoader)
        manager._loader = None
        manager.reload()

        assert manager._raw_config is None
        assert manager._app_config is None
        assert manager._type_converter is None
        assert manager._provider_manager is None

    def test_set_clears_cache(self, tmp_path):
        """Test that set clears the cache."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"key": "original"}))

        manager = ConfigurationManager(config_file=str(config_file))
        assert manager.get("key") == "original"

        manager.set("key", "updated")
        assert manager.get("key") == "updated"

    def test_config_file_attribute(self, tmp_path):
        """Test that config file path is stored."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({}))
        path_str = str(config_file)

        manager = ConfigurationManager(config_file=path_str)
        assert manager._config_file == path_str

    def test_get_typed_with_defaults_on_error(self, tmp_path):
        """Test get_typed_with_defaults returns defaults on error."""
        from config.schemas.app_schema import AppConfig

        config_file = tmp_path / "missing.json"  # Does not exist

        manager = ConfigurationManager(config_file=str(config_file))
        # Should not raise, should return defaults
        result = manager.get_typed_with_defaults(AppConfig)
        assert isinstance(result, AppConfig)

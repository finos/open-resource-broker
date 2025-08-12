"""Unit tests for ConfigurationManager."""

import json
import os
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

from src.config.manager import ConfigurationManager
from src.config.schemas.app_schema import AppConfig


@pytest.mark.unit
class TestConfigurationManager:
    """Test cases for ConfigurationManager."""

    def test_configuration_manager_initialization(self):
        """Test ConfigurationManager initialization."""
        manager = ConfigurationManager()

        assert manager._config == {}
        assert manager._config_file_path is None
        assert manager._legacy_config is None

    def test_load_from_dict(self, test_config_dict: Dict[str, Any]):
        """Test loading configuration from dictionary."""
        manager = ConfigurationManager()
        manager.load_from_dict(test_config_dict)

        assert manager._config == test_config_dict
        assert manager.get("aws.region") == "us-east-1"
        assert manager.get("logging.level") == "DEBUG"
        assert manager.get("database.type") == "sqlite"

    def test_load_from_file(self, test_config_file: Path):
        """Test loading configuration from file."""
        manager = ConfigurationManager()
        manager.load_from_file(str(test_config_file))

        assert manager._config_file_path == str(test_config_file)
        assert manager.get("aws.region") == "us-east-1"
        assert manager.get("logging.level") == "DEBUG"

    def test_load_from_file_not_found(self):
        """Test loading configuration from non-existent file."""
        manager = ConfigurationManager()

        with pytest.raises(FileNotFoundError):
            manager.load_from_file("/nonexistent/config.json")

    def test_load_from_file_invalid_json(self, temp_dir: Path):
        """Test loading configuration from invalid JSON file."""
        invalid_json_file = temp_dir / "invalid.json"
        with open(invalid_json_file, "w") as f:
            f.write("{ invalid json }")

        manager = ConfigurationManager()

        with pytest.raises(json.JSONDecodeError):
            manager.load_from_file(str(invalid_json_file))

    def test_get_configuration_value(self, config_manager: ConfigurationManager):
        """Test getting configuration values."""
        # Test nested key access
        assert config_manager.get("aws.region") == "us-east-1"
        assert config_manager.get("aws.profile") == "default"
        assert config_manager.get("logging.level") == "DEBUG"
        assert config_manager.get("database.type") == "sqlite"

        # Test direct key access
        aws_config = config_manager.get("aws")
        assert aws_config["region"] == "us-east-1"
        assert aws_config["profile"] == "default"

    def test_get_configuration_value_with_default(self, config_manager: ConfigurationManager):
        """Test getting configuration values with default."""
        # Existing key
        assert config_manager.get("aws.region", "default-region") == "us-east-1"

        # Non-existing key
        assert config_manager.get("nonexistent.key", "default-value") == "default-value"
        assert config_manager.get("aws.nonexistent", "default") == "default"

    def test_get_configuration_value_not_found(self, config_manager: ConfigurationManager):
        """Test getting non-existent configuration values."""
        assert config_manager.get("nonexistent.key") is None
        assert config_manager.get("aws.nonexistent") is None
        assert config_manager.get("completely.nonexistent.nested.key") is None

    def test_set_configuration_value(self, config_manager: ConfigurationManager):
        """Test setting configuration values."""
        # Set new value
        config_manager.set("new.key", "new-value")
        assert config_manager.get("new.key") == "new-value"

        # Update existing value
        config_manager.set("aws.region", "us-west-2")
        assert config_manager.get("aws.region") == "us-west-2"

        # Set nested value
        config_manager.set("new.nested.key", "nested-value")
        assert config_manager.get("new.nested.key") == "nested-value"

    def test_has_configuration_key(self, config_manager: ConfigurationManager):
        """Test checking if configuration key exists."""
        # Existing keys
        assert config_manager.has("aws")
        assert config_manager.has("aws.region")
        assert config_manager.has("logging.level")

        # Non-existing keys
        assert not config_manager.has("nonexistent")
        assert not config_manager.has("aws.nonexistent")
        assert not config_manager.has("completely.nonexistent.key")

    def test_get_all_configuration(self, config_manager: ConfigurationManager):
        """Test getting all configuration."""
        all_config = config_manager.get_all()

        assert "aws" in all_config
        assert "logging" in all_config
        assert "database" in all_config
        assert all_config["aws"]["region"] == "us-east-1"
        assert all_config["logging"]["level"] == "DEBUG"

    def test_environment_variable_override(self, config_manager: ConfigurationManager):
        """Test environment variable override."""
        with patch.dict(
            os.environ,
            {
                "AWS_REGION": "us-west-1",
                "LOG_LEVEL": "INFO",
                "DATABASE_NAME": "override.db",
            },
        ):
            # Test direct environment variable access
            assert config_manager.get_env("AWS_REGION") == "us-west-1"
            assert config_manager.get_env("LOG_LEVEL") == "INFO"
            assert config_manager.get_env("DATABASE_NAME") == "override.db"
            assert config_manager.get_env("NONEXISTENT_VAR") is None

            # Test with default
            assert config_manager.get_env("NONEXISTENT_VAR", "default") == "default"

    def test_configuration_validation(self, config_manager: ConfigurationManager):
        """Test configuration validation."""
        # Valid configuration should pass
        is_valid = config_manager.validate()
        assert is_valid is True

        # Test with invalid configuration
        config_manager.set("aws.region", "")  # Empty region should be invalid
        is_valid = config_manager.validate()
        assert is_valid is False

    def test_configuration_to_app_config(self, config_manager: ConfigurationManager):
        """Test converting configuration to AppConfig."""
        app_config = config_manager.to_app_config()

        assert isinstance(app_config, AppConfig)
        assert app_config.aws.region == "us-east-1"
        assert app_config.logging.level == "DEBUG"
        assert app_config.database.type == "sqlite"

    def test_configuration_merge(self):
        """Test merging configurations."""
        manager = ConfigurationManager()

        # Load base configuration
        base_config = {
            "aws": {"region": "us-east-1", "profile": "default"},
            "logging": {"level": "INFO"},
        }
        manager.load_from_dict(base_config)

        # Merge additional configuration
        additional_config = {
            "aws": {"access_key_id": "test-key"},  # Add to existing section
            "database": {"type": "sqlite"},  # Add new section
        }
        manager.merge(additional_config)

        # Verify merge results
        assert manager.get("aws.region") == "us-east-1"  # Original value preserved
        assert manager.get("aws.profile") == "default"  # Original value preserved
        assert manager.get("aws.access_key_id") == "test-key"  # New value added
        assert manager.get("logging.level") == "INFO"  # Original value preserved
        assert manager.get("database.type") == "sqlite"  # New section added

    def test_configuration_merge_override(self):
        """Test merging configurations with override."""
        manager = ConfigurationManager()

        # Load base configuration
        base_config = {
            "aws": {"region": "us-east-1", "profile": "default"},
            "logging": {"level": "INFO"},
        }
        manager.load_from_dict(base_config)

        # Merge with override
        override_config = {
            "aws": {"region": "us-west-2"},  # Override existing value
            # Override and add
            "logging": {"level": "DEBUG", "file_path": "logs/app.log"},
        }
        manager.merge(override_config, override=True)

        # Verify merge results
        assert manager.get("aws.region") == "us-west-2"  # Overridden value
        assert manager.get("aws.profile") == "default"  # Original value preserved
        assert manager.get("logging.level") == "DEBUG"  # Overridden value
        assert manager.get("logging.file_path") == "logs/app.log"  # New value added

    def test_configuration_save_to_file(self, config_manager: ConfigurationManager, temp_dir: Path):
        """Test saving configuration to file."""
        output_file = temp_dir / "output_config.json"

        # Save configuration
        config_manager.save_to_file(str(output_file))

        # Verify file was created and contains correct data
        assert output_file.exists()

        with open(output_file, "r") as f:
            saved_config = json.load(f)

        assert saved_config["aws"]["region"] == "us-east-1"
        assert saved_config["logging"]["level"] == "DEBUG"
        assert saved_config["database"]["type"] == "sqlite"

    def test_configuration_reload(self, test_config_file: Path):
        """Test reloading configuration from file."""
        manager = ConfigurationManager()
        manager.load_from_file(str(test_config_file))

        # Modify configuration in memory
        manager.set("aws.region", "us-west-2")
        assert manager.get("aws.region") == "us-west-2"

        # Reload from file
        manager.reload()

        # Verify configuration was reloaded
        assert manager.get("aws.region") == "us-east-1"  # Back to original value

    def test_configuration_reload_without_file(self):
        """Test reloading configuration when no file was loaded."""
        manager = ConfigurationManager()
        manager.load_from_dict({"test": "value"})

        # Should not raise exception, but should not change anything
        manager.reload()
        assert manager.get("test") == "value"

    def test_configuration_clear(self, config_manager: ConfigurationManager):
        """Test clearing configuration."""
        # Verify configuration exists
        assert config_manager.get("aws.region") == "us-east-1"

        # Clear configuration
        config_manager.clear()

        # Verify configuration is cleared
        assert config_manager.get("aws.region") is None
        assert config_manager.get_all() == {}

    def test_configuration_copy(self, config_manager: ConfigurationManager):
        """Test copying configuration."""
        copy_manager = config_manager.copy()

        # Verify copy has same values
        assert copy_manager.get("aws.region") == "us-east-1"
        assert copy_manager.get("logging.level") == "DEBUG"

        # Verify they are independent
        copy_manager.set("aws.region", "us-west-2")
        assert copy_manager.get("aws.region") == "us-west-2"
        assert config_manager.get("aws.region") == "us-east-1"  # Original unchanged

    def test_configuration_keys_iteration(self, config_manager: ConfigurationManager):
        """Test iterating over configuration keys."""
        keys = list(config_manager.keys())

        assert "aws" in keys
        assert "logging" in keys
        assert "database" in keys
        assert "template" in keys
        assert "REPOSITORY_CONFIG" in keys

    def test_configuration_values_iteration(self, config_manager: ConfigurationManager):
        """Test iterating over configuration values."""
        values = list(config_manager.values())

        # Check that we have the expected number of top-level values
        assert len(values) >= 5  # aws, logging, database, template, REPOSITORY_CONFIG

        # Check that values contain expected types
        aws_config = config_manager.get("aws")
        assert aws_config in values

    def test_configuration_items_iteration(self, config_manager: ConfigurationManager):
        """Test iterating over configuration items."""
        items = list(config_manager.items())

        # Check that we have key-value pairs
        assert len(items) >= 5

        # Check specific items
        aws_config = config_manager.get("aws")
        assert ("aws", aws_config) in items

    def test_configuration_nested_key_parsing(self):
        """Test parsing nested keys."""
        manager = ConfigurationManager()

        # Test various nested key formats
        config = {
            "level1": {"level2": {"level3": "deep_value"}},
            "simple": "simple_value",
        }
        manager.load_from_dict(config)

        # Test nested access
        assert manager.get("level1.level2.level3") == "deep_value"
        assert manager.get("simple") == "simple_value"

        # Test partial nested access
        level2 = manager.get("level1.level2")
        assert level2["level3"] == "deep_value"

    def test_configuration_type_preservation(self):
        """Test that configuration preserves data types."""
        manager = ConfigurationManager()

        config = {
            "string_value": "test",
            "int_value": 42,
            "float_value": 3.14,
            "bool_value": True,
            "list_value": [1, 2, 3],
            "dict_value": {"nested": "value"},
        }
        manager.load_from_dict(config)

        # Verify types are preserved
        assert isinstance(manager.get("string_value"), str)
        assert isinstance(manager.get("int_value"), int)
        assert isinstance(manager.get("float_value"), float)
        assert isinstance(manager.get("bool_value"), bool)
        assert isinstance(manager.get("list_value"), list)
        assert isinstance(manager.get("dict_value"), dict)

        # Verify values
        assert manager.get("string_value") == "test"
        assert manager.get("int_value") == 42
        assert manager.get("float_value") == 3.14
        assert manager.get("bool_value") is True
        assert manager.get("list_value") == [1, 2, 3]
        assert manager.get("dict_value") == {"nested": "value"}


@pytest.mark.unit
class TestConfigurationManagerEdgeCases:
    """Test edge cases for ConfigurationManager."""

    def test_empty_configuration(self):
        """Test handling empty configuration."""
        manager = ConfigurationManager()

        assert manager.get("any.key") is None
        assert manager.get("any.key", "default") == "default"
        assert not manager.has("any.key")
        assert manager.get_all() == {}
        assert list(manager.keys()) == []
        assert list(manager.values()) == []
        assert list(manager.items()) == []

    def test_none_values_in_configuration(self):
        """Test handling None values in configuration."""
        manager = ConfigurationManager()

        config = {
            "null_value": None,
            "empty_string": "",
            "zero_value": 0,
            "false_value": False,
        }
        manager.load_from_dict(config)

        # None should be returned as None
        assert manager.get("null_value") is None

        # Other falsy values should be preserved
        assert manager.get("empty_string") == ""
        assert manager.get("zero_value") == 0
        assert manager.get("false_value") is False

        # All keys should exist
        assert manager.has("null_value")
        assert manager.has("empty_string")
        assert manager.has("zero_value")
        assert manager.has("false_value")

    def test_special_characters_in_keys(self):
        """Test handling special characters in configuration keys."""
        manager = ConfigurationManager()

        config = {
            "key-with-dashes": "value1",
            "key_with_underscores": "value2",
            "key.with.dots": "value3",
            "key with spaces": "value4",
            "KEY_UPPERCASE": "value5",
        }
        manager.load_from_dict(config)

        # All keys should be accessible
        assert manager.get("key-with-dashes") == "value1"
        assert manager.get("key_with_underscores") == "value2"
        assert manager.get("key.with.dots") == "value3"
        assert manager.get("key with spaces") == "value4"
        assert manager.get("KEY_UPPERCASE") == "value5"

    def test_very_deep_nesting(self):
        """Test handling very deep nested configuration."""
        manager = ConfigurationManager()

        # Create deeply nested configuration
        config = {"level1": {"level2": {"level3": {"level4": {"level5": "deep_value"}}}}}
        manager.load_from_dict(config)

        # Should be able to access deep values
        assert manager.get("level1.level2.level3.level4.level5") == "deep_value"

        # Should be able to access intermediate levels
        level3 = manager.get("level1.level2.level3")
        assert level3["level4"]["level5"] == "deep_value"

    def test_large_configuration(self):
        """Test handling large configuration."""
        manager = ConfigurationManager()

        # Create large configuration
        config = {}
        for i in range(1000):
            config[f"key_{i}"] = f"value_{i}"

        manager.load_from_dict(config)

        # Should be able to access all values
        assert manager.get("key_0") == "value_0"
        assert manager.get("key_500") == "value_500"
        assert manager.get("key_999") == "value_999"

        # Should have correct number of keys
        assert len(list(manager.keys())) == 1000

    def test_circular_reference_prevention(self):
        """Test prevention of circular references in configuration."""
        manager = ConfigurationManager()

        # This should not cause infinite recursion
        config = {"section1": {"ref": "section2"}, "section2": {"ref": "section1"}}
        manager.load_from_dict(config)

        # Should be able to access values normally
        assert manager.get("section1.ref") == "section2"
        assert manager.get("section2.ref") == "section1"

    @patch("builtins.open", side_effect=PermissionError("Permission denied"))
    def test_file_permission_error(self, mock_open):
        """Test handling file permission errors."""
        manager = ConfigurationManager()

        with pytest.raises(PermissionError):
            manager.load_from_file("/restricted/config.json")

    @patch("builtins.open", side_effect=IOError("I/O error"))
    def test_file_io_error(self, mock_open):
        """Test handling file I/O errors."""
        manager = ConfigurationManager()

        with pytest.raises(IOError):
            manager.load_from_file("/problematic/config.json")

    def test_configuration_thread_safety(self, config_manager: ConfigurationManager):
        """Test basic thread safety of configuration operations."""
        import threading
        import time

        results = []
        errors = []

        def read_config():
            try:
                for _ in range(100):
                    value = config_manager.get("aws.region")
                    results.append(value)
                    # Small delay to increase chance of race conditions
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def write_config():
            try:
                for i in range(100):
                    config_manager.set(f"test.key_{i}", f"value_{i}")
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        # Run concurrent read and write operations
        threads = [
            threading.Thread(target=read_config),
            threading.Thread(target=write_config),
            threading.Thread(target=read_config),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Should not have any errors
        assert len(errors) == 0

        # Should have read values successfully
        assert len(results) == 200  # 2 read threads * 100 iterations each
        assert all(result == "us-east-1" for result in results)

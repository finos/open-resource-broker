"""Tests for configuration utilities."""

import json
import os
import tempfile

import pytest

from domain.base.exceptions import ConfigurationError
from infrastructure.utilities.config_utils import ConfigFileLoader


class TestConfigFileLoader:
    """Tests for ConfigFileLoader."""

    def test_load_json_file_success(self):
        """Test loading valid JSON file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"key": "value"}, f)
            temp_path = f.name

        try:
            result = ConfigFileLoader.load_json_file(temp_path)
            assert result == {"key": "value"}
        finally:
            os.unlink(temp_path)

    def test_load_json_file_not_found(self):
        """Test loading non-existent file returns None."""
        result = ConfigFileLoader.load_json_file("/nonexistent/file.json")
        assert result is None

    def test_load_json_file_invalid_json(self):
        """Test loading invalid JSON raises ConfigurationError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("invalid json {")
            temp_path = f.name

        try:
            with pytest.raises(ConfigurationError) as exc_info:
                ConfigFileLoader.load_json_file(temp_path)
            assert "Invalid JSON" in exc_info.value.message
        finally:
            os.unlink(temp_path)

    def test_merge_configs_simple(self):
        """Test simple config merge."""
        base = {"a": 1, "b": 2}
        update = {"b": 3, "c": 4}
        ConfigFileLoader.merge_configs(base, update)
        assert base == {"a": 1, "b": 3, "c": 4}

    def test_merge_configs_nested(self):
        """Test nested config merge."""
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        update = {"a": {"y": 20, "z": 30}}
        ConfigFileLoader.merge_configs(base, update)
        assert base == {"a": {"x": 1, "y": 20, "z": 30}, "b": 3}

    def test_merge_configs_array_replacement(self):
        """Test that arrays are replaced, not merged."""
        base = {"items": [1, 2, 3]}
        update = {"items": [4, 5]}
        ConfigFileLoader.merge_configs(base, update)
        assert base == {"items": [4, 5]}

    def test_convert_string_value_boolean(self):
        """Test converting string to boolean."""
        assert ConfigFileLoader.convert_string_value("true") is True
        assert ConfigFileLoader.convert_string_value("false") is False
        assert ConfigFileLoader.convert_string_value("True") is True

    def test_convert_string_value_integer(self):
        """Test converting string to integer."""
        assert ConfigFileLoader.convert_string_value("42") == 42
        assert ConfigFileLoader.convert_string_value("-10") == -10

    def test_convert_string_value_float(self):
        """Test converting string to float."""
        assert ConfigFileLoader.convert_string_value("3.14") == 3.14
        assert ConfigFileLoader.convert_string_value("-2.5") == -2.5

    def test_convert_string_value_json(self):
        """Test converting string to JSON."""
        result = ConfigFileLoader.convert_string_value('{"key": "value"}')
        assert result == {"key": "value"}

    def test_convert_string_value_string(self):
        """Test string remains string."""
        assert ConfigFileLoader.convert_string_value("hello") == "hello"

    def test_validate_config_path_exists(self):
        """Test validating existing path."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_path = f.name

        try:
            assert ConfigFileLoader.validate_config_path(temp_path) is True
        finally:
            os.unlink(temp_path)

    def test_validate_config_path_missing_optional(self):
        """Test validating missing optional path."""
        assert ConfigFileLoader.validate_config_path("/nonexistent", required=False) is False

    def test_validate_config_path_missing_required(self):
        """Test validating missing required path raises error."""
        with pytest.raises(ConfigurationError) as exc_info:
            ConfigFileLoader.validate_config_path("/nonexistent", required=True)
        assert "not found" in exc_info.value.message

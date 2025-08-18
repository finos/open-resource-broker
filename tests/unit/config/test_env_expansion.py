"""Tests for environment variable expansion utilities."""

import os
from unittest.mock import patch

from config.utils.env_expansion import expand_config_env_vars, expand_env_vars


class TestEnvironmentVariableExpansion:
    """Test environment variable expansion functionality."""

    def test_expand_simple_env_var(self):
        """Test expansion of simple environment variable."""
        with patch.dict(os.environ, {"TEST_VAR": "/test/path"}):
            result = expand_env_vars("$TEST_VAR")
            assert result == "/test/path"

    def test_expand_braced_env_var(self):
        """Test expansion of braced environment variable."""
        with patch.dict(os.environ, {"TEST_VAR": "/test/path"}):
            result = expand_env_vars("${TEST_VAR}")
            assert result == "/test/path"

    def test_expand_env_var_with_subpath(self):
        """Test expansion of environment variable with subpath."""
        with patch.dict(os.environ, {"TEST_VAR": "/test/path"}):
            result = expand_env_vars("$TEST_VAR/subdir")
            assert result == "/test/path/subdir"

    def test_expand_braced_env_var_with_subpath(self):
        """Test expansion of braced environment variable with subpath."""
        with patch.dict(os.environ, {"TEST_VAR": "/test/path"}):
            result = expand_env_vars("${TEST_VAR}/subdir")
            assert result == "/test/path/subdir"

    def test_expand_nonexistent_env_var(self):
        """Test expansion of non-existent environment variable."""
        result = expand_env_vars("$NONEXISTENT_VAR")
        assert result == "$NONEXISTENT_VAR"

    def test_expand_dict_values(self):
        """Test expansion of environment variables in dictionary values."""
        with patch.dict(os.environ, {"TEST_VAR": "/test/path"}):
            config = {"path": "$TEST_VAR/config", "other": "normal_value"}
            result = expand_env_vars(config)
            assert result == {"path": "/test/path/config", "other": "normal_value"}

    def test_expand_nested_dict_values(self):
        """Test expansion of environment variables in nested dictionary values."""
        with patch.dict(os.environ, {"TEST_VAR": "/test/path"}):
            config = {
                "scheduler": {"config_root": "$TEST_VAR/configs"},
                "other": "value",
            }
            result = expand_env_vars(config)
            assert result == {
                "scheduler": {"config_root": "/test/path/configs"},
                "other": "value",
            }

    def test_expand_list_values(self):
        """Test expansion of environment variables in list values."""
        with patch.dict(os.environ, {"TEST_VAR": "/test/path"}):
            config = ["$TEST_VAR/file1", "$TEST_VAR/file2", "normal_value"]
            result = expand_env_vars(config)
            assert result == ["/test/path/file1", "/test/path/file2", "normal_value"]

    def test_expand_non_string_values(self):
        """Test that non-string values are returned unchanged."""
        config = {"number": 42, "boolean": True, "none": None}
        result = expand_env_vars(config)
        assert result == config

    def test_expand_config_env_vars(self):
        """Test the main configuration expansion function."""
        with patch.dict(os.environ, {"HF_PROVIDER_CONFDIR": "/opt/hostfactory"}):
            config = {
                "scheduler": {
                    "type": "hostfactory",
                    "config_root": "$HF_PROVIDER_CONFDIR/configs",
                },
                "template": {"templates_file_name": "awsprov_templates.json"},
            }
            result = expand_config_env_vars(config)
            assert result["scheduler"]["config_root"] == "/opt/hostfactory/configs"
            assert result["template"]["templates_file_name"] == "awsprov_templates.json"

"""Unit tests for SDK configuration following existing test patterns."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from src.sdk.config import SDKConfig
from src.sdk.exceptions import ConfigurationError


class TestSDKConfig:
    """Test cases for SDKConfig following existing test patterns."""

    def test_default_configuration(self):
        """Test default configuration values."""
        config = SDKConfig()

        assert config.provider == "aws"
        assert config.region is None
        assert config.profile is None
        assert config.timeout == 300
        assert config.retry_attempts == 3
        assert config.log_level == "INFO"
        assert config.custom_config == {}

    def test_configuration_with_values(self):
        """Test configuration with explicit values."""
        config = SDKConfig(
            provider="mock",
            region="us-west-2",
            profile="test-profile",
            timeout=600,
            retry_attempts=5,
            log_level="DEBUG",
        )

        assert config.provider == "mock"
        assert config.region == "us-west-2"
        assert config.profile == "test-profile"
        assert config.timeout == 600
        assert config.retry_attempts == 5
        assert config.log_level == "DEBUG"

    def test_from_env_with_defaults(self):
        """Test creating configuration from environment with defaults."""
        with patch.dict(os.environ, {}, clear=True):
            config = SDKConfig.from_env()

            assert config.provider == "aws"
            assert config.region is None
            assert config.timeout == 300
            assert config.retry_attempts == 3
            assert config.log_level == "INFO"

    def test_from_env_with_values(self):
        """Test creating configuration from environment with values."""
        env_vars = {
            "OHFP_PROVIDER": "mock",
            "OHFP_REGION": "us-east-1",
            "OHFP_PROFILE": "test",
            "OHFP_TIMEOUT": "600",
            "OHFP_RETRY_ATTEMPTS": "5",
            "OHFP_LOG_LEVEL": "DEBUG",
            "OHFP_CONFIG_PATH": "/path/to/config",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config = SDKConfig.from_env()

            assert config.provider == "mock"
            assert config.region == "us-east-1"
            assert config.profile == "test"
            assert config.timeout == 600
            assert config.retry_attempts == 5
            assert config.log_level == "DEBUG"
            assert config.config_path == "/path/to/config"

    def test_from_dict_known_fields(self):
        """Test creating configuration from dictionary with known fields."""
        config_dict = {
            "provider": "mock",
            "region": "us-west-2",
            "timeout": 600,
            "log_level": "DEBUG",
        }

        config = SDKConfig.from_dict(config_dict)

        assert config.provider == "mock"
        assert config.region == "us-west-2"
        assert config.timeout == 600
        assert config.log_level == "DEBUG"

    def test_from_dict_with_custom_fields(self):
        """Test creating configuration from dictionary with custom fields."""
        config_dict = {
            "provider": "mock",
            "timeout": 600,
            "custom_field": "custom_value",
            "another_custom": 123,
        }

        config = SDKConfig.from_dict(config_dict)

        assert config.provider == "mock"
        assert config.timeout == 600
        assert config.custom_config["custom_field"] == "custom_value"
        assert config.custom_config["another_custom"] == 123

    def test_from_file_json(self):
        """Test creating configuration from JSON file."""
        config_data = {
            "provider": "mock",
            "region": "us-west-2",
            "timeout": 600,
            "custom_option": "test_value",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            temp_path = f.name

        try:
            config = SDKConfig.from_file(temp_path)

            assert config.provider == "mock"
            assert config.region == "us-west-2"
            assert config.timeout == 600
            assert config.custom_config["custom_option"] == "test_value"
            assert config.config_path == temp_path
        finally:
            os.unlink(temp_path)

    def test_from_file_nonexistent(self):
        """Test creating configuration from nonexistent file."""
        with pytest.raises(ConfigurationError, match="Configuration file not found"):
            SDKConfig.from_file("/nonexistent/path/config.json")

    def test_from_file_invalid_json(self):
        """Test creating configuration from invalid JSON file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("invalid json content")
            temp_path = f.name

        try:
            with pytest.raises(ConfigurationError, match="Failed to load configuration"):
                SDKConfig.from_file(temp_path)
        finally:
            os.unlink(temp_path)

    def test_to_dict(self):
        """Test converting configuration to dictionary."""
        config = SDKConfig(
            provider="mock",
            region="us-west-2",
            timeout=600,
            custom_config={"custom_field": "value"},
        )

        result = config.to_dict()

        assert result["provider"] == "mock"
        assert result["region"] == "us-west-2"
        assert result["timeout"] == 600
        assert result["custom_field"] == "value"
        assert "profile" not in result  # None values should be excluded

    def test_validate_success(self):
        """Test successful configuration validation."""
        config = SDKConfig(provider="mock", timeout=300,
                           retry_attempts=3, log_level="INFO")

        # Should not raise exception
        config.validate()

    def test_validate_empty_provider(self):
        """Test validation failure with empty provider."""
        config = SDKConfig(provider="")

        with pytest.raises(ConfigurationError, match="Provider is required"):
            config.validate()

    def test_validate_invalid_timeout(self):
        """Test validation failure with invalid timeout."""
        config = SDKConfig(timeout=0)

        with pytest.raises(ConfigurationError, match="Timeout must be positive"):
            config.validate()

    def test_validate_negative_retry_attempts(self):
        """Test validation failure with negative retry attempts."""
        config = SDKConfig(retry_attempts=-1)

        with pytest.raises(ConfigurationError, match="Retry attempts cannot be negative"):
            config.validate()

    def test_validate_invalid_log_level(self):
        """Test validation failure with invalid log level."""
        config = SDKConfig(log_level="INVALID")

        with pytest.raises(ConfigurationError, match="Invalid log level"):
            config.validate()

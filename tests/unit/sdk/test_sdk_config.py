"""Unit tests for SDKConfig validation and construction."""

import json

import pytest

from orb.sdk.config import SDKConfig
from orb.sdk.exceptions import ConfigurationError


class TestSDKConfigDefaults:
    def test_default_values(self):
        config = SDKConfig()
        assert config.provider == "aws"
        assert config.timeout == 300
        assert config.retry_attempts == 3
        assert config.log_level == "INFO"
        assert config.region is None
        assert config.profile is None
        assert config.config_path is None
        assert config.custom_config == {}

    def test_validate_passes_with_defaults(self):
        SDKConfig().validate()  # must not raise


class TestSDKConfigValidation:
    def test_empty_provider_raises(self):
        with pytest.raises(ConfigurationError, match="Provider is required"):
            SDKConfig(provider="").validate()

    def test_zero_timeout_raises(self):
        with pytest.raises(ConfigurationError, match="Timeout must be positive"):
            SDKConfig(timeout=0).validate()

    def test_negative_timeout_raises(self):
        with pytest.raises(ConfigurationError, match="Timeout must be positive"):
            SDKConfig(timeout=-1).validate()

    def test_negative_retry_raises(self):
        with pytest.raises(ConfigurationError, match="Retry attempts cannot be negative"):
            SDKConfig(retry_attempts=-1).validate()

    def test_invalid_log_level_raises(self):
        with pytest.raises(ConfigurationError, match="Invalid log level"):
            SDKConfig(log_level="VERBOSE").validate()

    def test_valid_log_levels(self):
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            SDKConfig(log_level=level).validate()

    def test_log_level_case_insensitive(self):
        SDKConfig(log_level="debug").validate()

    def test_zero_retry_is_valid(self):
        SDKConfig(retry_attempts=0).validate()


class TestSDKConfigFromEnv:
    def test_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("ORB_PROVIDER", "mock")
        monkeypatch.setenv("ORB_REGION", "eu-west-1")
        monkeypatch.setenv("ORB_TIMEOUT", "60")
        monkeypatch.setenv("ORB_RETRY_ATTEMPTS", "5")
        monkeypatch.setenv("ORB_LOG_LEVEL", "DEBUG")

        config = SDKConfig.from_env()
        assert config.provider == "mock"
        assert config.region == "eu-west-1"
        assert config.timeout == 60
        assert config.retry_attempts == 5
        assert config.log_level == "DEBUG"

    def test_defaults_when_env_absent(self, monkeypatch):
        for var in [
            "ORB_PROVIDER",
            "ORB_REGION",
            "ORB_TIMEOUT",
            "ORB_RETRY_ATTEMPTS",
            "ORB_LOG_LEVEL",
        ]:
            monkeypatch.delenv(var, raising=False)

        config = SDKConfig.from_env()
        assert config.provider == "aws"
        assert config.region is None
        assert config.timeout == 300


class TestSDKConfigFromDict:
    def test_known_fields_mapped(self):
        config = SDKConfig.from_dict({"provider": "mock", "timeout": 120, "region": "us-west-2"})
        assert config.provider == "mock"
        assert config.timeout == 120
        assert config.region == "us-west-2"

    def test_unknown_fields_go_to_custom_config(self):
        config = SDKConfig.from_dict({"provider": "aws", "my_custom_key": "value"})
        assert config.custom_config == {"my_custom_key": "value"}

    def test_empty_dict_uses_defaults(self):
        config = SDKConfig.from_dict({})
        assert config.provider == "aws"


class TestSDKConfigFromFile:
    def test_loads_json_file(self, tmp_path):
        data = {"provider": "mock", "timeout": 60}
        f = tmp_path / "config.json"
        f.write_text(json.dumps(data))

        config = SDKConfig.from_file(str(f))
        assert config.provider == "mock"
        assert config.timeout == 60
        assert config.config_path == str(f)

    def test_missing_file_raises(self):
        with pytest.raises(ConfigurationError, match="not found"):
            SDKConfig.from_file("/nonexistent/path/config.json")


class TestSDKConfigToDict:
    def test_to_dict_excludes_none(self):
        config = SDKConfig(provider="aws", region=None)
        d = config.to_dict()
        assert "region" not in d
        assert d["provider"] == "aws"

    def test_to_dict_includes_custom_config(self):
        config = SDKConfig(custom_config={"extra": "val"})
        d = config.to_dict()
        assert d["extra"] == "val"

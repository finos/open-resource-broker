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
        assert config.provider_config == {}
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
        monkeypatch.setenv("ORB_TIMEOUT", "60")
        monkeypatch.setenv("ORB_RETRY_ATTEMPTS", "5")
        monkeypatch.setenv("ORB_LOG_LEVEL", "DEBUG")

        config = SDKConfig.from_env()
        assert config.provider == "mock"
        assert config.timeout == 60
        assert config.retry_attempts == 5
        assert config.log_level == "DEBUG"

    def test_defaults_when_env_absent(self, monkeypatch):
        for var in [
            "ORB_PROVIDER",
            "ORB_TIMEOUT",
            "ORB_RETRY_ATTEMPTS",
            "ORB_LOG_LEVEL",
        ]:
            monkeypatch.delenv(var, raising=False)

        config = SDKConfig.from_env()
        assert config.provider == "aws"
        assert config.timeout == 300


class TestSDKConfigFromDict:
    def test_known_fields_mapped(self):
        config = SDKConfig.from_dict({"provider": "mock", "timeout": 120})
        assert config.provider == "mock"
        assert config.timeout == 120

    def test_unknown_fields_go_to_custom_config(self):
        config = SDKConfig.from_dict({"provider": "aws", "my_custom_key": "value"})
        assert config.custom_config == {"my_custom_key": "value"}

    def test_empty_dict_uses_defaults(self):
        config = SDKConfig.from_dict({})
        assert config.provider == "aws"

    def test_scheduler_dict_not_absorbed_as_string_override(self):
        # Regression: when loading from an ORB config.json the top-level "scheduler" key
        # is a nested config dict {"type": "hostfactory", "config_root": "..."}.
        # from_dict must NOT ingest that dict as the SDKConfig.scheduler string override
        # because that propagates a dict into ConfigurationManager.override_scheduler_strategy
        # which then surfaces as "unhashable type: 'dict'" deep in the DI factory chain.
        orb_config = {
            "provider": {"type": "aws"},
            "scheduler": {"type": "hostfactory", "config_root": "$ORB_CONFIG_DIR"},
        }
        config = SDKConfig.from_dict(orb_config)
        # scheduler field must remain None (no string override set)
        assert config.scheduler is None
        # the original dict is preserved in custom_config so nothing is silently dropped
        assert config.custom_config.get("scheduler") == {
            "type": "hostfactory",
            "config_root": "$ORB_CONFIG_DIR",
        }

    def test_scheduler_string_override_is_still_accepted(self):
        # Explicit string scheduler overrides (e.g. passed programmatically) must still work.
        config = SDKConfig.from_dict({"provider": "aws", "scheduler": "default"})
        assert config.scheduler == "default"

    def test_provider_config_dict_mapped(self):
        config = SDKConfig.from_dict(
            {"provider": "aws", "provider_config": {"region": "us-east-1", "profile": "prod"}}
        )
        assert config.provider_config == {"region": "us-east-1", "profile": "prod"}

    def test_legacy_top_level_region_folded_into_provider_config(self):
        # A caller that still passes region= at the top level gets it folded into
        # provider_config (not custom_config) via the deprecation shim.
        with pytest.warns(DeprecationWarning, match="deprecated"):
            config = SDKConfig.from_dict({"provider": "aws", "region": "eu-central-1"})
        assert config.provider_config.get("region") == "eu-central-1"
        assert "region" not in config.custom_config


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
        config = SDKConfig(provider="aws")
        d = config.to_dict()
        assert "region" not in d
        assert "profile" not in d
        assert "provider_config" not in d
        assert d["provider"] == "aws"

    def test_to_dict_includes_provider_config_when_set(self):
        config = SDKConfig(provider_config={"region": "eu-west-1"})
        d = config.to_dict()
        assert d["provider_config"] == {"region": "eu-west-1"}

    def test_to_dict_includes_custom_config(self):
        config = SDKConfig(custom_config={"extra": "val"})
        d = config.to_dict()
        assert d["extra"] == "val"


class TestSDKConfigProviderTypeAndName:
    def test_fields_default_to_none(self):
        config = SDKConfig()
        assert config.provider_type is None
        assert config.provider_name is None

    def test_all_three_provider_fields_are_independent(self):
        config = SDKConfig(provider="aws", provider_type="k8s", provider_name="my-k8s-instance")
        assert config.provider == "aws"
        assert config.provider_type == "k8s"
        assert config.provider_name == "my-k8s-instance"

    def test_to_dict_includes_provider_type_and_name_when_set(self):
        config = SDKConfig(provider="aws", provider_type="k8s", provider_name="my-k8s-instance")
        d = config.to_dict()
        assert d["provider"] == "aws"
        assert d["provider_type"] == "k8s"
        assert d["provider_name"] == "my-k8s-instance"

    def test_to_dict_omits_provider_type_and_name_when_none(self):
        config = SDKConfig(provider="aws")
        d = config.to_dict()
        assert "provider_type" not in d
        assert "provider_name" not in d


class TestDeprecatedRegionProfile:
    """Backward-compatibility shims for the removed region / profile surface."""

    # --- SDKConfig.from_dict top-level keys ---

    def test_from_dict_legacy_region_key_warns_and_populates_provider_config(self):
        with pytest.warns(DeprecationWarning, match="deprecated"):
            config = SDKConfig.from_dict({"provider": "aws", "region": "us-east-1"})
        assert config.provider_config.get("region") == "us-east-1"
        assert "region" not in config.custom_config

    def test_from_dict_legacy_profile_key_warns_and_populates_provider_config(self):
        with pytest.warns(DeprecationWarning, match="deprecated"):
            config = SDKConfig.from_dict({"provider": "aws", "profile": "my-profile"})
        assert config.provider_config.get("profile") == "my-profile"
        assert "profile" not in config.custom_config

    def test_from_dict_legacy_keys_do_not_override_explicit_provider_config(self):
        # Existing provider_config values take precedence (setdefault semantics).
        with pytest.warns(DeprecationWarning):
            config = SDKConfig.from_dict(
                {
                    "provider": "aws",
                    "region": "us-east-1",
                    "provider_config": {"region": "eu-west-1"},
                }
            )
        assert config.provider_config["region"] == "eu-west-1"

    # --- SDKConfig.from_env legacy env vars ---

    def test_from_env_legacy_orb_region_warns_and_populates_provider_config(self, monkeypatch):
        monkeypatch.setenv("ORB_REGION", "ap-southeast-1")
        monkeypatch.delenv("ORB_PROFILE", raising=False)
        with pytest.warns(DeprecationWarning, match="deprecated"):
            config = SDKConfig.from_env()
        assert config.provider_config.get("region") == "ap-southeast-1"

    def test_from_env_legacy_orb_profile_warns_and_populates_provider_config(self, monkeypatch):
        monkeypatch.setenv("ORB_PROFILE", "staging")
        monkeypatch.delenv("ORB_REGION", raising=False)
        with pytest.warns(DeprecationWarning, match="deprecated"):
            config = SDKConfig.from_env()
        assert config.provider_config.get("profile") == "staging"

    def test_from_env_no_legacy_vars_no_warning(self, monkeypatch):
        monkeypatch.delenv("ORB_REGION", raising=False)
        monkeypatch.delenv("ORB_PROFILE", raising=False)
        # Should not raise or warn.
        config = SDKConfig.from_env()
        assert config.provider_config == {}

    # --- SDKConfig.region property (read) ---

    def test_region_property_read_warns_and_returns_provider_config_value(self):
        config = SDKConfig(provider_config={"region": "us-west-2"})
        with pytest.warns(DeprecationWarning, match="deprecated"):
            value = config.region
        assert value == "us-west-2"

    def test_region_property_read_returns_none_when_absent(self):
        config = SDKConfig()
        with pytest.warns(DeprecationWarning):
            value = config.region
        assert value is None

    # --- SDKConfig.region property (write) ---

    def test_region_setter_warns_and_updates_provider_config(self):
        config = SDKConfig()
        with pytest.warns(DeprecationWarning, match="deprecated"):
            config.region = "us-east-2"
        assert config.provider_config["region"] == "us-east-2"

    def test_region_setter_none_removes_from_provider_config(self):
        config = SDKConfig(provider_config={"region": "us-east-1"})
        with pytest.warns(DeprecationWarning):
            config.region = None
        assert "region" not in config.provider_config

    # --- SDKConfig.profile property (read) ---

    def test_profile_property_read_warns_and_returns_provider_config_value(self):
        config = SDKConfig(provider_config={"profile": "prod"})
        with pytest.warns(DeprecationWarning, match="deprecated"):
            value = config.profile
        assert value == "prod"

    def test_profile_property_read_returns_none_when_absent(self):
        config = SDKConfig()
        with pytest.warns(DeprecationWarning):
            value = config.profile
        assert value is None

    # --- SDKConfig.profile property (write) ---

    def test_profile_setter_warns_and_updates_provider_config(self):
        config = SDKConfig()
        with pytest.warns(DeprecationWarning, match="deprecated"):
            config.profile = "dev"
        assert config.provider_config["profile"] == "dev"

    def test_profile_setter_none_removes_from_provider_config(self):
        config = SDKConfig(provider_config={"profile": "prod"})
        with pytest.warns(DeprecationWarning):
            config.profile = None
        assert "profile" not in config.provider_config

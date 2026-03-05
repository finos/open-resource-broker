"""Tests for ConfigurationLoader environment variable handling."""

from config.loader import ConfigurationLoader


class TestLoaderEnv:
    def test_legacy_log_console_alias_not_read(self, monkeypatch):
        monkeypatch.delenv("ORB_LOG_CONSOLE_ENABLED", raising=False)
        monkeypatch.setenv("LOG_CONSOLE_ENABLED", "false")
        config = {}
        ConfigurationLoader._load_from_env(config)
        assert "console_enabled" not in config.get("logging", {})

    def test_canonical_log_console_var_is_read(self, monkeypatch):
        monkeypatch.setenv("ORB_LOG_CONSOLE_ENABLED", "false")
        config = {}
        ConfigurationLoader._load_from_env(config)
        assert config["logging"]["console_enabled"] is False

    def test_orb_config_file_is_read(self, monkeypatch):
        monkeypatch.setenv("ORB_CONFIG_FILE", "/tmp/test.json")
        config = {}
        ConfigurationLoader._load_from_env(config)
        assert config["config_file"] == "/tmp/test.json"

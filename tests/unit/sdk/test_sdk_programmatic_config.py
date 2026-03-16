"""Unit tests for SDK programmatic config (ORBClient app_config parameter)."""

from unittest.mock import MagicMock

from orb.bootstrap import Application
from orb.config.managers.configuration_manager import ConfigurationManager
from orb.sdk.client import ORBClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_CONFIG = {
    "provider": {"type": "mock"},
}

NESTED_CONFIG = {
    "provider": {"type": "aws"},
    "logging": {"level": "INFO"},
    "storage": {"strategy": "memory"},
}


# ---------------------------------------------------------------------------
# ConfigurationManager with config_dict
# ---------------------------------------------------------------------------


class TestConfigurationManagerWithDict:
    def test_accepts_config_dict_without_file(self):
        cm = ConfigurationManager(config_dict=MINIMAL_CONFIG)
        assert cm._config_dict is MINIMAL_CONFIG

    def test_config_file_is_none_when_only_dict_provided(self):
        cm = ConfigurationManager(config_dict=MINIMAL_CONFIG)
        # No file path required — config_file may be None
        assert cm._config_file is None

    def test_ensure_raw_config_contains_dict_values(self):
        cm = ConfigurationManager(config_dict=NESTED_CONFIG)
        raw = cm._ensure_raw_config()
        assert raw["provider"]["type"] == "aws"
        assert raw["logging"]["level"] == "INFO"
        assert raw["storage"]["strategy"] == "memory"

    def test_ensure_raw_config_cached_on_second_call(self):
        cm = ConfigurationManager(config_dict=NESTED_CONFIG)
        first = cm._ensure_raw_config()
        second = cm._ensure_raw_config()
        assert first is second

    def test_get_reads_top_level_key(self):
        cm = ConfigurationManager(config_dict=NESTED_CONFIG)
        assert cm.get("provider.type") == "aws"

    def test_get_reads_nested_key_with_dot_notation(self):
        cm = ConfigurationManager(config_dict=NESTED_CONFIG)
        assert cm.get("provider.type") == "aws"

    def test_get_returns_default_for_missing_key(self):
        cm = ConfigurationManager(config_dict=MINIMAL_CONFIG)
        assert cm.get("nonexistent", "fallback") == "fallback"

    def test_get_raw_config_returns_copy(self):
        cm = ConfigurationManager(config_dict=NESTED_CONFIG)
        raw = cm.get_raw_config()
        assert raw["provider"]["type"] == "aws"
        assert raw["logging"]["level"] == "INFO"
        assert raw["storage"]["strategy"] == "memory"
        assert raw is not NESTED_CONFIG  # must be a copy

    def test_config_dict_takes_precedence_over_file(self):
        # Even if a config_file path is supplied alongside config_dict,
        # _ensure_raw_config must use the dict (no file I/O).
        cm = ConfigurationManager(
            config_file="/nonexistent/path/config.json",
            config_dict=MINIMAL_CONFIG,
        )
        raw = cm._ensure_raw_config()
        assert raw["provider"]["type"] == "mock"

    def test_no_file_io_when_config_dict_provided(self):
        # Constructing with a non-existent path + config_dict must not raise.
        cm = ConfigurationManager(
            config_file="/does/not/exist.json",
            config_dict=MINIMAL_CONFIG,
        )
        raw = cm._ensure_raw_config()
        assert raw["provider"]["type"] == "mock"


# ---------------------------------------------------------------------------
# Application with config_dict
# ---------------------------------------------------------------------------


class TestApplicationWithConfigDict:
    def test_stores_config_dict(self):
        app = Application(config_dict=MINIMAL_CONFIG, skip_validation=True)
        assert app.config_dict is MINIMAL_CONFIG

    def test_stores_none_when_not_provided(self):
        app = Application(skip_validation=True)
        assert app.config_dict is None

    def test_config_path_and_config_dict_both_stored(self):
        app = Application(
            config_path="/some/path.json",
            config_dict=NESTED_CONFIG,
            skip_validation=True,
        )
        assert app.config_path == "/some/path.json"
        assert app.config_dict is NESTED_CONFIG

    def test_not_initialized_before_initialize_called(self):
        app = Application(config_dict=MINIMAL_CONFIG, skip_validation=True)
        assert app._initialized is False

    def test_ensure_container_registers_config_manager_when_dict_provided(self):
        """_ensure_container pre-registers a ConfigurationManager when config_dict is set."""
        app = Application(config_dict=MINIMAL_CONFIG, skip_validation=True)

        # Patch the DI container so we can inspect register_instance calls
        mock_container = MagicMock()
        mock_container.is_lazy_loading_enabled = MagicMock(return_value=True)

        import orb.infrastructure.di.container as container_module

        original_get_container = container_module.get_container
        container_module.get_container = lambda: mock_container

        try:
            app._ensure_container()
        finally:
            container_module.get_container = original_get_container

        # Verify register_instance was called with ConfigurationManager
        calls = mock_container.register_instance.call_args_list
        registered_types = [call.args[0] for call in calls]
        assert ConfigurationManager in registered_types

        # Verify the registered instance carries the correct config_dict
        cm_call = next(c for c in calls if c.args[0] is ConfigurationManager)
        registered_cm: ConfigurationManager = cm_call.args[1]
        assert registered_cm._config_dict is MINIMAL_CONFIG

    def test_ensure_container_skips_registration_when_no_dict(self):
        """_ensure_container must NOT pre-register ConfigurationManager when config_dict is None."""
        app = Application(skip_validation=True)

        mock_container = MagicMock()

        import orb.infrastructure.di.container as container_module

        original_get_container = container_module.get_container
        container_module.get_container = lambda: mock_container

        try:
            app._ensure_container()
        finally:
            container_module.get_container = original_get_container

        registered_types = [
            call.args[0] for call in mock_container.register_instance.call_args_list
        ]
        assert ConfigurationManager not in registered_types


# ---------------------------------------------------------------------------
# ORBClient app_config parameter
# ---------------------------------------------------------------------------


class TestORBClientAppConfig:
    def test_stores_app_config(self):
        sdk = ORBClient(app_config=MINIMAL_CONFIG)
        assert sdk._app_config is MINIMAL_CONFIG

    def test_app_config_none_by_default(self):
        sdk = ORBClient()
        assert sdk._app_config is None

    def test_app_config_preserved_alongside_sdk_config(self):
        sdk_cfg = {"provider": "mock", "timeout": 30}
        sdk = ORBClient(config=sdk_cfg, app_config=NESTED_CONFIG)
        assert sdk._app_config is NESTED_CONFIG

    def test_not_initialized_after_construction(self):
        sdk = ORBClient(app_config=MINIMAL_CONFIG)
        assert sdk._initialized is False

    def test_app_config_passed_to_application_on_initialize(self):
        """initialize() must forward app_config as config_dict= to Application."""
        sdk = ORBClient(app_config=MINIMAL_CONFIG)

        captured = {}

        class CapturingApplication:
            def __init__(self, **kwargs):
                captured["config_dict"] = kwargs.get("config_dict")
                # Raise immediately so initialize() bails out early — we only
                # care that the argument was forwarded correctly.
                raise RuntimeError("stop after capture")

        import orb.sdk.client as client_module

        original_cls = client_module.Application
        client_module.Application = CapturingApplication

        try:
            import asyncio

            asyncio.run(sdk.initialize())
        except Exception:
            pass
        finally:
            client_module.Application = original_cls

        assert captured.get("config_dict") is MINIMAL_CONFIG

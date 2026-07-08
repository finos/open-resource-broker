"""Unit tests for OpenResourceBroker client initialization and introspection."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.sdk.client import OpenResourceBroker
from orb.sdk.config import SDKConfig
from orb.sdk.exceptions import ConfigurationError, ProviderError, SDKError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sdk(provider="aws", **kwargs) -> OpenResourceBroker:
    """Create an SDK instance without triggering env-var lookup side-effects."""
    return OpenResourceBroker(config={"provider": provider, **kwargs})


def _initialized_sdk(extra_methods: dict | None = None) -> OpenResourceBroker:
    """Return an SDK instance that is already marked as initialized with mock buses."""
    sdk = _make_sdk()
    sdk._initialized = True
    sdk._query_bus = AsyncMock()
    sdk._command_bus = AsyncMock()

    from orb.sdk.discovery import SDKMethodDiscovery

    sdk._discovery = SDKMethodDiscovery()

    methods = extra_methods or {}
    sdk._methods = methods
    for name, fn in methods.items():
        setattr(sdk, name, fn)
    return sdk


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestOpenResourceBrokerConstruction:
    def test_default_provider_is_aws(self):
        sdk = _make_sdk()
        assert sdk.provider == "aws"

    def test_explicit_provider_stored(self):
        sdk = OpenResourceBroker(config={"provider": "mock"})
        assert sdk.provider == "mock"

    def test_config_dict_accepted(self):
        sdk = OpenResourceBroker(config={"provider": "aws", "timeout": 60})
        assert sdk.config.timeout == 60

    def test_config_path_accepted(self, tmp_path):
        import json

        f = tmp_path / "cfg.json"
        f.write_text(json.dumps({"provider": "aws"}))
        sdk = OpenResourceBroker(config_path=str(f))
        assert sdk.config.config_path == str(f)

    def test_kwargs_go_to_custom_config(self):
        sdk = OpenResourceBroker(config={"provider": "aws"}, my_key="my_val")
        assert sdk.config.custom_config.get("my_key") == "my_val"

    def test_invalid_config_raises_configuration_error(self):
        with pytest.raises(ConfigurationError):
            OpenResourceBroker(config={"provider": "", "timeout": 300})

    def test_not_initialized_on_construction(self):
        sdk = _make_sdk()
        assert not sdk.initialized

    def test_repr_not_initialized(self):
        sdk = _make_sdk()
        assert "not initialized" in repr(sdk)
        assert "aws" in repr(sdk)

    # F3 — SDKConfig instance accepted directly in constructor
    def test_config_as_sdk_config_instance_used_directly(self):
        """ORBClient(config=SDKConfig(...)) must not raise AttributeError.

        Regression: when config was an SDKConfig instance, the constructor
        called SDKConfig.from_dict(config) which expects a dict, causing an
        AttributeError deep in from_dict's iteration over config.items().
        """
        sdk_config = SDKConfig(provider="aws", timeout=120)
        sdk = OpenResourceBroker(config=sdk_config)
        assert sdk.config is sdk_config
        assert sdk.config.provider == "aws"
        assert sdk.config.timeout == 120

    def test_config_as_dict_still_works(self):
        """Passing a dict for config must still produce a valid SDKConfig."""
        sdk = OpenResourceBroker(config={"provider": "aws", "timeout": 60})
        assert sdk.config.provider == "aws"
        assert sdk.config.timeout == 60

    def test_config_as_sdk_config_instance_respects_explicit_provider_kwarg(self):
        """Explicit provider= kwarg must override the value in the SDKConfig instance."""
        sdk_config = SDKConfig(provider="aws")
        sdk = OpenResourceBroker(config=sdk_config, provider="mock")
        assert sdk.config.provider == "mock"


# ---------------------------------------------------------------------------
# initialize() / context manager
# ---------------------------------------------------------------------------


class TestOpenResourceBrokerInitialize:
    @pytest.mark.asyncio
    async def test_initialize_returns_true_on_success(self):
        sdk = _make_sdk()
        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=True)
        mock_app.get_query_bus.return_value = AsyncMock()
        mock_app.get_command_bus.return_value = AsyncMock()

        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery") as mock_disc_cls,
        ):
            mock_disc = MagicMock()
            mock_disc.discover_cqrs_methods = AsyncMock(return_value={})
            mock_disc_cls.return_value = mock_disc

            result = await sdk.initialize()

        assert result is True
        assert sdk.initialized

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self):
        sdk = _make_sdk()
        sdk._initialized = True
        result = await sdk.initialize()
        assert result is True

    @pytest.mark.asyncio
    async def test_initialize_raises_provider_error_when_app_fails(self):
        sdk = _make_sdk()
        mock_app = AsyncMock()
        mock_app.initialize.return_value = False

        with patch("orb.sdk.client.Application", return_value=mock_app):
            with pytest.raises(ProviderError):
                await sdk.initialize()

    @pytest.mark.asyncio
    async def test_initialize_raises_configuration_error_when_buses_missing(self):
        sdk = _make_sdk()
        # initialize() is async but get_query_bus/get_command_bus are synchronous —
        # use MagicMock for those so .return_value = None is not wrapped in a coroutine
        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=True)
        mock_app.get_query_bus.return_value = None
        mock_app.get_command_bus.return_value = None

        with patch("orb.sdk.client.Application", return_value=mock_app):
            with pytest.raises(ConfigurationError, match="CQRS buses not available"):
                await sdk.initialize()

    @pytest.mark.asyncio
    async def test_context_manager_initializes_and_cleans_up(self):
        sdk = _make_sdk()
        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=True)
        mock_app.get_query_bus.return_value = AsyncMock()
        mock_app.get_command_bus.return_value = AsyncMock()
        mock_app.cleanup = AsyncMock()

        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery") as mock_disc_cls,
        ):
            mock_disc = MagicMock()
            mock_disc.discover_cqrs_methods = AsyncMock(return_value={})
            mock_disc.list_available_methods.return_value = []
            mock_disc_cls.return_value = mock_disc

            async with sdk as s:
                assert s.initialized

        assert not sdk.initialized

    @pytest.mark.asyncio
    async def test_cleanup_resets_state(self):
        sdk = _initialized_sdk()
        mock_app = AsyncMock()
        mock_app.cleanup = AsyncMock()
        sdk._app = mock_app

        await sdk.cleanup()

        assert not sdk.initialized
        assert sdk._methods == {}


# ---------------------------------------------------------------------------
# Introspection methods (require initialized state)
# ---------------------------------------------------------------------------


class TestOpenResourceBrokerIntrospection:
    def test_list_available_methods_raises_when_not_initialized(self):
        sdk = _make_sdk()
        with pytest.raises(SDKError, match="not initialized"):
            sdk.list_available_methods()

    def test_list_available_methods_returns_method_names(self):
        sdk = _initialized_sdk({"list_templates": AsyncMock(), "get_request": AsyncMock()})
        methods = sdk.list_available_methods()
        assert "list_templates" in methods
        assert "get_request" in methods

    def test_get_method_info_raises_when_not_initialized(self):
        sdk = _make_sdk()
        with pytest.raises(SDKError, match="not initialized"):
            sdk.get_method_info("list_templates")

    def test_get_method_info_returns_none_for_unknown(self):
        sdk = _initialized_sdk()
        assert sdk.get_method_info("nonexistent_method") is None

    def test_get_methods_by_type_raises_when_not_initialized(self):
        sdk = _make_sdk()
        with pytest.raises(SDKError, match="not initialized"):
            sdk.get_methods_by_type("query")

    def test_get_methods_by_type_returns_empty_list_when_no_discovery(self):
        sdk = _initialized_sdk()
        sdk._discovery = None
        assert sdk.get_methods_by_type("query") == []

    def test_get_stats_not_initialized(self):
        sdk = _make_sdk()
        stats = sdk.get_stats()
        assert stats["initialized"] is False
        assert stats["methods_discovered"] == 0

    def test_get_stats_initialized(self):
        sdk = _initialized_sdk({"list_templates": AsyncMock()})
        stats = sdk.get_stats()
        assert stats["initialized"] is True
        assert stats["methods_discovered"] == 1
        assert "list_templates" in stats["available_methods"]

    def test_repr_initialized(self):
        sdk = _initialized_sdk({"m1": AsyncMock()})
        r = repr(sdk)
        assert "initialized" in r
        assert "1" in r

    def test_config_property(self):
        sdk = _make_sdk()
        assert isinstance(sdk.config, SDKConfig)

    def test_provider_property(self):
        sdk = _make_sdk(provider="aws")
        assert sdk.provider == "aws"


# ---------------------------------------------------------------------------
# Convenience methods
# ---------------------------------------------------------------------------


class TestConvenienceMethods:
    @pytest.mark.asyncio
    async def test_request_machines_raises_when_not_initialized(self):
        sdk = _make_sdk()
        with pytest.raises(SDKError, match="not initialized"):
            await sdk.request_machines("tmpl-1", 5)

    @pytest.mark.asyncio
    async def test_request_machines_delegates_to_orchestrator(self):
        from orb.application.services.orchestration.dtos import AcquireMachinesOutput

        sdk = _initialized_sdk()
        mock_container = MagicMock()
        sdk._container = mock_container

        mock_orchestrator = MagicMock()
        mock_orchestrator.execute = AsyncMock(
            return_value=AcquireMachinesOutput(
                request_id="req-123",
                status="pending",
            )
        )
        mock_container.get.return_value = mock_orchestrator
        mock_container.get_optional.return_value = None

        result = await sdk.request_machines("tmpl-1", 3)

        assert result == {"request_id": "req-123", "status": "pending", "machine_ids": []}

    @pytest.mark.asyncio
    async def test_show_template_raises_when_not_initialized(self):
        sdk = _make_sdk()
        with pytest.raises(SDKError, match="not initialized"):
            await sdk.show_template("tmpl-1")

    @pytest.mark.asyncio
    async def test_show_template_delegates_to_get_template(self):
        sdk = _initialized_sdk()
        mock_get = AsyncMock(return_value={"template_id": "tmpl-1"})
        sdk.get_template = mock_get  # type: ignore[attr-defined]

        result = await sdk.show_template("tmpl-1")

        mock_get.assert_awaited_once_with(template_id="tmpl-1")
        assert result == {"template_id": "tmpl-1"}

    @pytest.mark.asyncio
    async def test_health_check_raises_when_not_initialized(self):
        sdk = _make_sdk()
        with pytest.raises(SDKError, match="not initialized"):
            await sdk.health_check()

    @pytest.mark.asyncio
    async def test_health_check_delegates_to_get_provider_health(self):
        sdk = _initialized_sdk()
        mock_health = AsyncMock(return_value={"status": "healthy"})
        sdk.get_provider_health = mock_health  # type: ignore[attr-defined]

        result = await sdk.health_check()

        mock_health.assert_awaited_once()
        assert result == {"status": "healthy"}


# ---------------------------------------------------------------------------
# sys.exit / missing config safety
# ---------------------------------------------------------------------------


class TestInitializeMissingConfigSafety:
    @pytest.mark.asyncio
    async def test_missing_config_raises_configuration_error_not_system_exit(self):
        """initialize() must raise ConfigurationError, never kill the process."""
        sdk = OpenResourceBroker(config={"provider": "aws"})

        with patch("orb.sdk.client.Application", side_effect=SystemExit(1)):
            with pytest.raises(ConfigurationError):
                await sdk.initialize()

    @pytest.mark.asyncio
    async def test_missing_config_does_not_raise_system_exit(self):
        """SystemExit must never propagate out of initialize()."""
        sdk = OpenResourceBroker(config={"provider": "aws"})

        with patch("orb.sdk.client.Application", side_effect=SystemExit(1)):
            try:
                await sdk.initialize()
            except ConfigurationError:
                pass  # expected
            except SystemExit:
                pytest.fail("initialize() raised SystemExit — process would have been killed")

    def test_nonexistent_config_path_raises_configuration_error(self, tmp_path):
        """A config_path that does not exist raises ConfigurationError at construction."""
        with pytest.raises(ConfigurationError, match="not found"):
            OpenResourceBroker(config_path=str(tmp_path / "does_not_exist.json"))

    @pytest.mark.asyncio
    async def test_downstream_sys_exit_converted_to_configuration_error(self):
        """If Application.__init__ somehow calls sys.exit, it is caught and converted."""
        sdk = OpenResourceBroker(config={"provider": "aws", "dummy": "value"})
        sdk._config.config_path = None

        with patch("orb.sdk.client.Application", side_effect=SystemExit(1)):
            with pytest.raises(ConfigurationError, match="exit code 1"):
                await sdk.initialize()


# ---------------------------------------------------------------------------
# Deprecated region / profile constructor kwargs
# ---------------------------------------------------------------------------


class TestDeprecatedRegionProfile:
    """Backward-compatibility shims for the removed region / profile constructor surface."""

    def test_region_kwarg_warns_and_populates_provider_config(self):
        with pytest.warns(DeprecationWarning, match="deprecated"):
            sdk = OpenResourceBroker(config={"provider": "aws"}, region="us-east-1")
        assert sdk.config.provider_config.get("region") == "us-east-1"

    def test_profile_kwarg_warns_and_populates_provider_config(self):
        with pytest.warns(DeprecationWarning, match="deprecated"):
            sdk = OpenResourceBroker(config={"provider": "aws"}, profile="my-profile")
        assert sdk.config.provider_config.get("profile") == "my-profile"

    def test_region_and_profile_together_warn_once_and_both_set(self):
        with pytest.warns(DeprecationWarning, match="deprecated"):
            sdk = OpenResourceBroker(
                config={"provider": "aws"}, region="eu-west-1", profile="staging"
            )
        assert sdk.config.provider_config.get("region") == "eu-west-1"
        assert sdk.config.provider_config.get("profile") == "staging"

    def test_deprecated_region_does_not_override_explicit_provider_config(self):
        # provider_config takes precedence over the deprecated kwarg (setdefault semantics).
        with pytest.warns(DeprecationWarning):
            sdk = OpenResourceBroker(
                config={"provider": "aws"},
                provider_config={"region": "eu-central-1"},
                region="us-east-1",
            )
        assert sdk.config.provider_config.get("region") == "eu-central-1"

    def test_no_warning_when_neither_region_nor_profile_passed(self):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            # Must not raise.
            OpenResourceBroker(config={"provider": "aws"})

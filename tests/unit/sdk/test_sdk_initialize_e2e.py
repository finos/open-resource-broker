"""SDK end-to-end initialize tests using a real CQRS bus stub.

Tests initialize(), SDKConfig.from_env, get_method_parameters,
SDKError wrapping, get_stats, and cleanup — all without real AWS or network calls.
The CQRS buses are stubbed at the Application level so discover_cqrs_methods
runs for real against the registered handlers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.config import SDKConfig
from orb.sdk.exceptions import ConfigurationError, ProviderError, SDKError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_mocks(methods: dict | None = None):
    """Return (mock_app, mock_disc) wired for a successful initialize()."""
    mock_app = MagicMock()
    mock_app.initialize = AsyncMock(return_value=True)
    mock_app.get_query_bus.return_value = AsyncMock()
    mock_app.get_command_bus.return_value = AsyncMock()
    mock_app.cleanup = AsyncMock()

    mock_disc = MagicMock()
    mock_disc.discover_cqrs_methods = AsyncMock(return_value=methods or {})
    mock_disc.list_available_methods.return_value = list((methods or {}).keys())
    mock_disc.get_method_info.return_value = None

    return mock_app, mock_disc


def _patched_init(methods: dict | None = None):
    """Context manager that patches Application + SDKMethodDiscovery."""
    mock_app, mock_disc = _make_app_mocks(methods)
    return (
        patch("orb.sdk.client.Application", return_value=mock_app),
        patch("orb.sdk.client.SDKMethodDiscovery", return_value=mock_disc),
        patch("orb.sdk.client.create_container", return_value=MagicMock()),
        mock_app,
        mock_disc,
    )


# ---------------------------------------------------------------------------
# initialize() with real method discovery
# ---------------------------------------------------------------------------


class TestInitializeWithRealDiscovery:
    @pytest.mark.asyncio
    async def test_initialize_discovers_cqrs_methods(self):
        """initialize() calls discover_cqrs_methods and populates _methods."""
        sdk = ORBClient(config={"provider": "aws"})
        discovered = {"list_templates": AsyncMock(), "create_request": AsyncMock()}

        mock_app, mock_disc = _make_app_mocks(discovered)
        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery", return_value=mock_disc),
            patch("orb.sdk.client.create_container", return_value=MagicMock()),
        ):
            result = await sdk.initialize()

        assert result is True
        assert sdk.initialized
        assert "list_templates" in sdk._methods
        assert "create_request" in sdk._methods

    @pytest.mark.asyncio
    async def test_initialize_sets_methods_as_attributes(self):
        """initialize() attaches discovered methods as instance attributes."""
        sdk = ORBClient(config={"provider": "aws"})
        discovered = {"list_templates": AsyncMock(), "get_request": AsyncMock()}

        mock_app, mock_disc = _make_app_mocks(discovered)
        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery", return_value=mock_disc),
            patch("orb.sdk.client.create_container", return_value=MagicMock()),
        ):
            await sdk.initialize()

        assert hasattr(sdk, "list_templates")
        assert hasattr(sdk, "get_request")

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self):
        """Calling initialize() twice returns True without re-running discovery."""
        sdk = ORBClient(config={"provider": "aws"})
        mock_app, mock_disc = _make_app_mocks()
        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery", return_value=mock_disc),
            patch("orb.sdk.client.create_container", return_value=MagicMock()),
        ):
            await sdk.initialize()
            result = await sdk.initialize()

        assert result is True
        # discover_cqrs_methods must only be called once
        mock_disc.discover_cqrs_methods.assert_awaited_once()


# ---------------------------------------------------------------------------
# SDKConfig.from_env via env var injection
# ---------------------------------------------------------------------------


class TestSDKConfigFromEnv:
    def test_from_env_reads_provider(self, monkeypatch):
        monkeypatch.setenv("ORB_PROVIDER", "mock")
        cfg = SDKConfig.from_env()
        assert cfg.provider == "mock"

    def test_from_env_reads_timeout(self, monkeypatch):
        monkeypatch.setenv("ORB_TIMEOUT", "120")
        cfg = SDKConfig.from_env()
        assert cfg.timeout == 120

    def test_from_env_reads_log_level(self, monkeypatch):
        monkeypatch.setenv("ORB_LOG_LEVEL", "DEBUG")
        cfg = SDKConfig.from_env()
        assert cfg.log_level == "DEBUG"

    def test_from_env_defaults_when_vars_absent(self, monkeypatch):
        for var in ("ORB_PROVIDER", "ORB_TIMEOUT", "ORB_LOG_LEVEL"):
            monkeypatch.delenv(var, raising=False)
        cfg = SDKConfig.from_env()
        assert cfg.provider == "aws"
        assert cfg.timeout == 300
        assert cfg.log_level == "INFO"

    def test_from_env_config_file_path(self, monkeypatch, tmp_path):
        import json

        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text(json.dumps({"provider": "aws"}))
        monkeypatch.setenv("ORB_CONFIG_FILE", str(cfg_file))
        cfg = SDKConfig.from_env()
        assert cfg.config_path == str(cfg_file)


# ---------------------------------------------------------------------------
# get_method_parameters against a discovered method
# ---------------------------------------------------------------------------


class TestGetMethodParameters:
    @pytest.mark.asyncio
    async def test_get_method_parameters_returns_dict_for_known_method(self):
        """get_method_parameters returns a dict for a method with MethodInfo."""
        from orb.sdk.discovery import MethodInfo

        sdk = ORBClient(config={"provider": "aws"})
        mock_method_info = MethodInfo(
            name="list_templates",
            description="List Templates - Query operation",
            parameters={"active_only": {"type": bool, "required": False}},
            required_params=[],
            return_type=None,
            handler_type="query",
            original_class=MagicMock(),
        )

        mock_app, mock_disc = _make_app_mocks({"list_templates": AsyncMock()})
        mock_disc.get_method_info.return_value = mock_method_info

        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery", return_value=mock_disc),
            patch("orb.sdk.client.create_container", return_value=MagicMock()),
            patch(
                "orb.sdk.parameter_mapping.ParameterMapper.get_supported_parameters",
                return_value={"active_only": "active_only"},
            ),
        ):
            await sdk.initialize()
            params = sdk.get_method_parameters("list_templates")

        assert params is not None
        assert isinstance(params, dict)

    def test_get_method_parameters_raises_when_not_initialized(self):
        sdk = ORBClient(config={"provider": "aws"})
        with pytest.raises(SDKError, match="not initialized"):
            sdk.get_method_parameters("list_templates")

    def test_get_method_parameters_returns_none_for_unknown_method(self):
        sdk = ORBClient(config={"provider": "aws"})
        sdk._initialized = True
        sdk._discovery = MagicMock()
        sdk._discovery.get_method_info.return_value = None
        result = sdk.get_method_parameters("nonexistent_method")
        assert result is None


# ---------------------------------------------------------------------------
# SDKError wrapping — initialize() with bus that raises
# ---------------------------------------------------------------------------


class TestSDKErrorWrapping:
    @pytest.mark.asyncio
    async def test_initialize_wraps_generic_exception_as_sdk_error(self):
        """A generic exception from Application is wrapped in SDKError."""
        sdk = ORBClient(config={"provider": "aws"})
        with patch("orb.sdk.client.Application", side_effect=ValueError("unexpected")):
            with pytest.raises(SDKError, match="SDK initialization failed"):
                await sdk.initialize()

    @pytest.mark.asyncio
    async def test_initialize_wraps_system_exit_as_configuration_error(self):
        """SystemExit from Application is wrapped in ConfigurationError."""
        sdk = ORBClient(config={"provider": "aws"})
        with patch("orb.sdk.client.Application", side_effect=SystemExit(1)):
            with pytest.raises(ConfigurationError):
                await sdk.initialize()

    @pytest.mark.asyncio
    async def test_initialize_raises_provider_error_when_app_init_fails(self):
        """ProviderError raised when Application.initialize() returns False."""
        sdk = ORBClient(config={"provider": "aws"})
        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=False)
        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.create_container", return_value=MagicMock()),
        ):
            with pytest.raises(ProviderError):
                await sdk.initialize()

    @pytest.mark.asyncio
    async def test_initialize_raises_configuration_error_when_buses_none(self):
        """ConfigurationError raised when buses are not available."""
        sdk = ORBClient(config={"provider": "aws"})
        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=True)
        mock_app.get_query_bus.return_value = None
        mock_app.get_command_bus.return_value = None
        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.create_container", return_value=MagicMock()),
        ):
            with pytest.raises(ConfigurationError, match="CQRS buses not available"):
                await sdk.initialize()

    @pytest.mark.asyncio
    async def test_sdk_error_preserves_message(self):
        """SDKError message is accessible via .message attribute."""
        err = SDKError("something went wrong", details={"key": "val"})
        assert err.message == "something went wrong"
        assert err.details == {"key": "val"}
        assert "something went wrong" in str(err)


# ---------------------------------------------------------------------------
# get_stats returns counts matching discovered methods
# ---------------------------------------------------------------------------


class TestGetStats:
    @pytest.mark.asyncio
    async def test_get_stats_counts_match_discovered_methods(self):
        """get_stats() method counts match what was discovered."""
        from orb.sdk.discovery import MethodInfo

        sdk = ORBClient(config={"provider": "aws"})

        query_info = MethodInfo(
            name="list_templates",
            description="",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="query",
            original_class=MagicMock(),
        )
        command_info = MethodInfo(
            name="create_request",
            description="",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="command",
            original_class=MagicMock(),
        )

        discovered = {
            "list_templates": AsyncMock(),
            "create_request": AsyncMock(),
        }
        mock_app, mock_disc = _make_app_mocks(discovered)

        def _get_info(name):
            return {"list_templates": query_info, "create_request": command_info}.get(name)

        mock_disc.get_method_info.side_effect = _get_info
        mock_disc.list_available_methods.return_value = list(discovered.keys())

        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery", return_value=mock_disc),
            patch("orb.sdk.client.create_container", return_value=MagicMock()),
        ):
            await sdk.initialize()
            stats = sdk.get_stats()

        assert stats["initialized"] is True
        assert stats["methods_discovered"] == 2
        assert "list_templates" in stats["available_methods"]
        assert "create_request" in stats["available_methods"]

    def test_get_stats_before_initialize(self):
        sdk = ORBClient(config={"provider": "aws"})
        stats = sdk.get_stats()
        assert stats["initialized"] is False
        assert stats["methods_discovered"] == 0


# ---------------------------------------------------------------------------
# cleanup() removes dynamically-added attributes
# ---------------------------------------------------------------------------


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_removes_dynamic_attributes(self):
        """cleanup() removes attributes that were set by initialize()."""
        sdk = ORBClient(config={"provider": "aws"})
        discovered = {"list_templates": AsyncMock(), "create_request": AsyncMock()}

        mock_app, mock_disc = _make_app_mocks(discovered)
        mock_disc.list_available_methods.return_value = list(discovered.keys())

        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery", return_value=mock_disc),
            patch("orb.sdk.client.create_container", return_value=MagicMock()),
        ):
            await sdk.initialize()
            assert hasattr(sdk, "list_templates")
            assert hasattr(sdk, "create_request")

            await sdk.cleanup()

        assert not sdk.initialized
        # Dynamic overrides are removed from the instance dict; class-level stubs remain
        assert "list_templates" not in sdk.__dict__
        assert "create_request" not in sdk.__dict__

    @pytest.mark.asyncio
    async def test_cleanup_resets_methods_dict(self):
        """cleanup() clears _methods dict."""
        sdk = ORBClient(config={"provider": "aws"})
        discovered = {"list_templates": AsyncMock()}

        mock_app, mock_disc = _make_app_mocks(discovered)
        mock_disc.list_available_methods.return_value = list(discovered.keys())

        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery", return_value=mock_disc),
            patch("orb.sdk.client.create_container", return_value=MagicMock()),
        ):
            await sdk.initialize()
            await sdk.cleanup()

        assert sdk._methods == {}

    @pytest.mark.asyncio
    async def test_context_manager_cleans_up_on_exit(self):
        """Async context manager calls cleanup on exit."""
        sdk = ORBClient(config={"provider": "aws"})
        discovered = {"list_templates": AsyncMock()}

        mock_app, mock_disc = _make_app_mocks(discovered)
        mock_disc.list_available_methods.return_value = list(discovered.keys())

        with (
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery", return_value=mock_disc),
            patch("orb.sdk.client.create_container", return_value=MagicMock()),
        ):
            async with sdk:
                assert sdk.initialized

        assert not sdk.initialized

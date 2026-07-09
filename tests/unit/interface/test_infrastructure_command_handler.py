"""Unit tests for infrastructure_command_handler.

NOTE: All handlers in this module bypass CQRS — they read config.json directly
(_get_active_providers) and use service-locator get_container() inside private helpers
instead of dispatching through a command/query bus. Tests reflect current behaviour
and include TODO markers where the violations exist.
"""

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest


def _ns(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _write_config(config_dir, data: dict) -> None:
    (config_dir / "config.json").write_text(json.dumps(data))


def _two_provider_config(config_dir) -> None:
    _write_config(
        config_dir,
        {
            "provider": {
                "providers": [
                    {
                        "name": "aws-a",
                        "type": "aws",
                        "enabled": True,
                        "config": {"profile": "a", "region": "us-east-1"},
                    },
                    {
                        "name": "aws-b",
                        "type": "aws",
                        "enabled": True,
                        "config": {"profile": "b", "region": "eu-west-1"},
                    },
                ]
            }
        },
    )


def _mock_container_with_provider_port(discover_return=None, validate_return=None):
    """Return a mock container whose ProviderPort supports discover/validate."""
    # TODO: service-locator anti-pattern — container is fetched inside private helpers
    container = MagicMock()
    provider_strategy = MagicMock()
    provider_strategy.discover_infrastructure.return_value = (
        discover_return if discover_return is not None else {"provider": "aws-a", "resources": []}
    )
    if validate_return is not None:
        provider_strategy.validate_infrastructure.return_value = validate_return
    else:
        provider_strategy.validate_infrastructure.return_value = {
            "provider": "aws-a",
            "valid": True,
        }
    container.get.return_value = provider_strategy
    return container


# ---------------------------------------------------------------------------
# handle_infrastructure_discover
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleInfrastructureDiscover:
    @pytest.mark.asyncio
    async def test_single_provider_returns_success(self, tmp_path):
        # TODO: CQRS violation — reads config.json and uses service-locator get_container()
        from orb.interface.infrastructure_command_handler import handle_infrastructure_discover

        _two_provider_config(tmp_path)
        mock_container = _mock_container_with_provider_port()

        with (
            patch(
                "orb.interface.infrastructure_command_handler.get_config_location",
                return_value=tmp_path,
            ),
        ):
            args = _ns(provider_name="aws-a")

            args._container = mock_container
            result = await handle_infrastructure_discover(args)

        assert result["status"] == "success"
        assert "providers" in result

    @pytest.mark.asyncio
    async def test_all_providers_returns_success(self, tmp_path):
        # TODO: CQRS violation — reads config.json and uses service-locator get_container()
        from orb.interface.infrastructure_command_handler import handle_infrastructure_discover

        _two_provider_config(tmp_path)
        mock_container = _mock_container_with_provider_port()

        with (
            patch(
                "orb.interface.infrastructure_command_handler.get_config_location",
                return_value=tmp_path,
            ),
        ):
            args = _ns(provider_name=None, all_providers=True)

            args._container = mock_container
            result = await handle_infrastructure_discover(args)

        assert result["status"] == "success"
        assert len(result["providers"]) == 2

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_error(self, tmp_path):
        # TODO: CQRS violation — reads config.json directly
        from orb.interface.infrastructure_command_handler import handle_infrastructure_discover

        _two_provider_config(tmp_path)
        mock_container = _mock_container_with_provider_port()

        with patch(
            "orb.interface.infrastructure_command_handler.get_config_location",
            return_value=tmp_path,
        ):
            args = _ns(provider_name="nonexistent")
            args._container = mock_container
            result = await handle_infrastructure_discover(args)

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# handle_infrastructure_show
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleInfrastructureShow:
    @pytest.mark.asyncio
    async def test_single_provider_returns_success(self, tmp_path):
        # TODO: CQRS violation — reads config.json directly and uses service-locator get_container()
        from orb.interface.infrastructure_command_handler import handle_infrastructure_show

        _two_provider_config(tmp_path)
        mock_container = _mock_container_with_provider_port()

        with (
            patch(
                "orb.interface.infrastructure_command_handler.get_config_location",
                return_value=tmp_path,
            ),
        ):
            args = _ns(provider_name="aws-a")

            args._container = mock_container
            result = await handle_infrastructure_show(args)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_all_providers_returns_success(self, tmp_path):
        # TODO: CQRS violation — reads config.json directly and uses service-locator get_container()
        from orb.interface.infrastructure_command_handler import handle_infrastructure_show

        _two_provider_config(tmp_path)
        mock_container = _mock_container_with_provider_port()

        with (
            patch(
                "orb.interface.infrastructure_command_handler.get_config_location",
                return_value=tmp_path,
            ),
        ):
            args = _ns(provider_name=None, all_providers=True)

            args._container = mock_container
            result = await handle_infrastructure_show(args)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_error(self, tmp_path):
        # TODO: CQRS violation — reads config.json directly
        from orb.interface.infrastructure_command_handler import handle_infrastructure_show

        _two_provider_config(tmp_path)
        mock_container = _mock_container_with_provider_port()

        with patch(
            "orb.interface.infrastructure_command_handler.get_config_location",
            return_value=tmp_path,
        ):
            args = _ns(provider_name="nonexistent")
            args._container = mock_container
            result = await handle_infrastructure_show(args)

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# handle_infrastructure_validate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleInfrastructureValidate:
    @pytest.mark.asyncio
    async def test_single_provider_returns_success(self, tmp_path):
        # TODO: CQRS violation — reads config.json and uses service-locator get_container()
        from orb.interface.infrastructure_command_handler import handle_infrastructure_validate

        _two_provider_config(tmp_path)
        mock_container = _mock_container_with_provider_port()

        with (
            patch(
                "orb.interface.infrastructure_command_handler.get_config_location",
                return_value=tmp_path,
            ),
        ):
            args = _ns(provider_name="aws-a")

            args._container = mock_container
            result = await handle_infrastructure_validate(args)

        assert result["status"] == "success"
        assert "providers" in result

    @pytest.mark.asyncio
    async def test_no_provider_specified_returns_success(self, tmp_path):
        # TODO: CQRS violation — reads config.json and uses service-locator get_container()
        from orb.interface.infrastructure_command_handler import handle_infrastructure_validate

        _two_provider_config(tmp_path)
        mock_container = _mock_container_with_provider_port()

        with (
            patch(
                "orb.interface.infrastructure_command_handler.get_config_location",
                return_value=tmp_path,
            ),
        ):
            args = _ns(provider_name=None)

            args._container = mock_container
            result = await handle_infrastructure_validate(args)

        assert result["status"] == "success"
        assert len(result["providers"]) == 2

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_error(self, tmp_path):
        # TODO: CQRS violation — reads config.json directly
        from orb.interface.infrastructure_command_handler import handle_infrastructure_validate

        _two_provider_config(tmp_path)
        mock_container = _mock_container_with_provider_port()

        with patch(
            "orb.interface.infrastructure_command_handler.get_config_location",
            return_value=tmp_path,
        ):
            args = _ns(provider_name="nonexistent")
            args._container = mock_container
            result = await handle_infrastructure_validate(args)

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# _get_active_providers — no "aws" hardcoded fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetActiveProvidersRegistryFallback:
    def test_no_config_file_uses_registry_first_type(self, tmp_path):
        """When no config.json exists, use first registered type (not hardcoded 'aws')."""
        from unittest.mock import MagicMock, patch

        from orb.interface.infrastructure_command_handler import _get_active_providers

        mock_registry_service = MagicMock()
        mock_registry_service.get_registered_provider_types.return_value = ["gcp"]
        mock_container = MagicMock()
        mock_container.get.return_value = mock_registry_service

        with (
            patch(
                "orb.interface.infrastructure_command_handler.get_config_location",
                return_value=tmp_path,
            ),
        ):
            providers = _get_active_providers(mock_container)

        assert providers[0]["type"] == "gcp"

    def test_no_config_file_no_registered_types_raises(self, tmp_path):
        """When no config.json and no registered types, raise RuntimeError."""
        from unittest.mock import MagicMock, patch

        from orb.interface.infrastructure_command_handler import _get_active_providers

        mock_registry_service = MagicMock()
        mock_registry_service.get_registered_provider_types.return_value = []
        mock_container = MagicMock()
        mock_container.get.return_value = mock_registry_service

        with (
            patch(
                "orb.interface.infrastructure_command_handler.get_config_location",
                return_value=tmp_path,
            ),
        ):
            with pytest.raises(RuntimeError, match="No providers are registered"):
                _get_active_providers(mock_container)

    def test_all_providers_disabled_no_registered_types_raises(self, tmp_path):
        """When all providers disabled and no registered types, raise RuntimeError."""
        from unittest.mock import MagicMock, patch

        from orb.interface.infrastructure_command_handler import _get_active_providers

        _write_config(
            tmp_path,
            {
                "provider": {
                    "providers": [
                        {
                            "name": "aws-a",
                            "type": "aws",
                            "enabled": False,
                        }
                    ]
                }
            },
        )

        mock_registry_service = MagicMock()
        mock_registry_service.get_registered_provider_types.return_value = []
        mock_container = MagicMock()
        mock_container.get.return_value = mock_registry_service

        with (
            patch(
                "orb.interface.infrastructure_command_handler.get_config_location",
                return_value=tmp_path,
            ),
        ):
            with pytest.raises(RuntimeError, match="No providers are registered"):
                _get_active_providers(mock_container)


# ---------------------------------------------------------------------------
# _show_provider_infrastructure — CLISpecRegistry-driven display
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestShowProviderInfrastructure:
    def test_known_provider_type_uses_spec_format_display(self):
        """Known provider types use CLISpecRegistry.format_display for display."""
        from unittest.mock import MagicMock, patch

        from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
        from orb.interface.infrastructure_command_handler import _show_provider_infrastructure

        mock_spec = MagicMock()
        mock_spec.format_display.return_value = [("Zone", "us-central1-a"), ("Project", "my-proj")]
        mock_console = MagicMock()
        mock_container = MagicMock()
        mock_container.get.return_value = mock_console

        provider = {
            "name": "gcp-main",
            "type": "gcp",
            "config": {"zone": "us-central1-a", "project": "my-proj"},
        }

        with (
            patch.object(CLISpecRegistry, "get_or_none", return_value=mock_spec),
        ):
            _show_provider_infrastructure(provider, mock_container)

        mock_spec.format_display.assert_called_once_with(provider["config"])
        # Ensure format_display output was written to console
        info_calls = [call.args[0] for call in mock_console.info.call_args_list]
        assert any("Zone" in c for c in info_calls)
        assert any("us-central1-a" in c for c in info_calls)

    def test_unknown_provider_type_falls_back_to_raw_config(self):
        """Unknown provider types fall back to raw key/value display."""
        from unittest.mock import MagicMock, patch

        from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
        from orb.interface.infrastructure_command_handler import _show_provider_infrastructure

        mock_console = MagicMock()
        mock_container = MagicMock()
        mock_container.get.return_value = mock_console

        provider = {
            "name": "unknown-1",
            "type": "unknown",
            "config": {"endpoint": "https://example.com"},
        }

        with (
            patch.object(CLISpecRegistry, "get_or_none", return_value=None),
        ):
            _show_provider_infrastructure(provider, mock_container)

        info_calls = [call.args[0] for call in mock_console.info.call_args_list]
        assert any("https://example.com" in c for c in info_calls)

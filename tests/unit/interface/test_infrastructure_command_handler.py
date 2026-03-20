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
            patch(
                "orb.interface.infrastructure_command_handler.get_container",
                return_value=mock_container,
            ),
        ):
            args = _ns(provider="aws-a")
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
            patch(
                "orb.interface.infrastructure_command_handler.get_container",
                return_value=mock_container,
            ),
        ):
            args = _ns(provider=None, all_providers=True)
            result = await handle_infrastructure_discover(args)

        assert result["status"] == "success"
        assert len(result["providers"]) == 2

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_error(self, tmp_path):
        # TODO: CQRS violation — reads config.json directly
        from orb.interface.infrastructure_command_handler import handle_infrastructure_discover

        _two_provider_config(tmp_path)

        with patch(
            "orb.interface.infrastructure_command_handler.get_config_location",
            return_value=tmp_path,
        ):
            args = _ns(provider="nonexistent")
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
            patch(
                "orb.interface.infrastructure_command_handler.get_container",
                return_value=mock_container,
            ),
        ):
            args = _ns(provider="aws-a")
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
            patch(
                "orb.interface.infrastructure_command_handler.get_container",
                return_value=mock_container,
            ),
        ):
            args = _ns(provider=None, all_providers=True)
            result = await handle_infrastructure_show(args)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_error(self, tmp_path):
        # TODO: CQRS violation — reads config.json directly
        from orb.interface.infrastructure_command_handler import handle_infrastructure_show

        _two_provider_config(tmp_path)

        with patch(
            "orb.interface.infrastructure_command_handler.get_config_location",
            return_value=tmp_path,
        ):
            args = _ns(provider="nonexistent")
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
            patch(
                "orb.interface.infrastructure_command_handler.get_container",
                return_value=mock_container,
            ),
        ):
            args = _ns(provider="aws-a")
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
            patch(
                "orb.interface.infrastructure_command_handler.get_container",
                return_value=mock_container,
            ),
        ):
            args = _ns(provider=None)
            result = await handle_infrastructure_validate(args)

        assert result["status"] == "success"
        assert len(result["providers"]) == 2

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_error(self, tmp_path):
        # TODO: CQRS violation — reads config.json directly
        from orb.interface.infrastructure_command_handler import handle_infrastructure_validate

        _two_provider_config(tmp_path)

        with patch(
            "orb.interface.infrastructure_command_handler.get_config_location",
            return_value=tmp_path,
        ):
            args = _ns(provider="nonexistent")
            result = await handle_infrastructure_validate(args)

        assert result["status"] == "error"

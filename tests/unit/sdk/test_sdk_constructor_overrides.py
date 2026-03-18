"""Tests for ORBClient scheduler constructor override wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.config import SDKConfig

# ---------------------------------------------------------------------------
# SDKConfig.from_dict
# ---------------------------------------------------------------------------


class TestSDKConfigScheduler:
    def test_scheduler_parsed_from_dict(self):
        config = SDKConfig.from_dict({"scheduler": "hostfactory"})
        assert config.scheduler == "hostfactory"

    def test_scheduler_not_in_custom_config(self):
        config = SDKConfig.from_dict({"scheduler": "hostfactory"})
        assert "scheduler" not in config.custom_config

    def test_scheduler_default_is_none(self):
        config = SDKConfig.from_dict({})
        assert config.scheduler is None


# ---------------------------------------------------------------------------
# ORBClient.initialize() — scheduler override
# ---------------------------------------------------------------------------


def _make_initialize_mocks():
    """Return (mock_app, mock_container, mock_config_port, mock_cm) wired for initialize()."""
    mock_config_port = MagicMock()
    mock_config_port.override_provider_region = MagicMock()
    mock_config_port.override_provider_profile = MagicMock()

    mock_cm = MagicMock()
    mock_cm.override_scheduler_strategy = MagicMock()

    mock_container = MagicMock()
    mock_container.get = MagicMock(return_value=mock_config_port)
    mock_container.get_optional = MagicMock(return_value=None)
    mock_container.register_instance = MagicMock()

    mock_app = MagicMock()
    mock_app.initialize = AsyncMock(return_value=True)
    mock_app.get_query_bus = MagicMock(return_value=AsyncMock())
    mock_app.get_command_bus = MagicMock(return_value=AsyncMock())

    return mock_app, mock_container, mock_config_port, mock_cm


@pytest.mark.asyncio
class TestORBClientSchedulerOverride:
    async def _init_client(self, config_dict):
        mock_app, mock_container, mock_config_port, mock_cm = _make_initialize_mocks()

        with (
            patch("orb.sdk.client.create_container", return_value=mock_container),
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery") as mock_discovery_cls,
            patch("orb.sdk.client.ConfigurationManager", return_value=mock_cm) as mock_cm_cls,
        ):
            mock_discovery = MagicMock()
            mock_discovery.discover_cqrs_methods = AsyncMock(return_value={})
            mock_discovery.list_available_methods = MagicMock(return_value=[])
            mock_discovery_cls.return_value = mock_discovery

            sdk = ORBClient(config=config_dict)
            await sdk.initialize()

        return mock_config_port, mock_cm, mock_container, mock_cm_cls

    async def test_hostfactory_scheduler_calls_override(self):
        _, mock_cm, mock_container, mock_cm_cls = await self._init_client(
            {"scheduler": "hostfactory"}
        )
        _, mock_cm, mock_container, mock_cm_cls = await self._init_client(
            {"scheduler": "hostfactory"}
        )
        mock_cm.override_scheduler_strategy.assert_called_once_with("hostfactory")
        mock_container.register_instance.assert_called_once_with(mock_cm_cls, mock_cm)

    async def test_default_scheduler_calls_override(self):
        _, mock_cm, mock_container, mock_cm_cls = await self._init_client({"scheduler": "default"})
        mock_cm.override_scheduler_strategy.assert_called_once_with("default")
        mock_container.register_instance.assert_called_once_with(mock_cm_cls, mock_cm)

    async def test_no_scheduler_does_not_call_override(self):
        _, mock_cm, mock_container, mock_cm_cls = await self._init_client({})
        mock_cm.override_scheduler_strategy.assert_not_called()
        mock_container.register_instance.assert_not_called()
        mock_cm_cls.assert_not_called()

    async def test_region_override_still_called(self):
        """Regression: existing region override must still work."""
        mock_config_port, _, _, _ = await self._init_client({"region": "us-east-2"})
        mock_config_port.override_provider_region.assert_called_once_with("us-east-2")

    async def test_scheduler_kwarg_calls_override(self):
        """Top-level scheduler= kwarg must reach override_scheduler_strategy."""
        mock_app, mock_container, mock_config_port, mock_cm = _make_initialize_mocks()

        with (
            patch("orb.sdk.client.create_container", return_value=mock_container),
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery") as mock_discovery_cls,
            patch("orb.sdk.client.ConfigurationManager", return_value=mock_cm) as mock_cm_cls,
        ):
            mock_discovery = MagicMock()
            mock_discovery.discover_cqrs_methods = AsyncMock(return_value={})
            mock_discovery.list_available_methods = MagicMock(return_value=[])
            mock_discovery_cls.return_value = mock_discovery

            sdk = ORBClient(scheduler="hostfactory")
            await sdk.initialize()

        mock_cm.override_scheduler_strategy.assert_called_once_with("hostfactory")
        mock_container.register_instance.assert_called_once_with(mock_cm_cls, mock_cm)

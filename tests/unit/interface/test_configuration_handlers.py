"""Unit tests for configuration management CLI handlers."""

from argparse import Namespace
from unittest.mock import AsyncMock, Mock

import pytest

from orb.interface.command_handlers import (
    handle_provider_config,
    handle_reload_provider_config,
    handle_validate_provider_config,
)


class TestProviderConfigHandlers:
    """Test provider configuration handler functionality."""

    @pytest.mark.asyncio
    async def test_handle_provider_config(self):
        """Test handle_provider_config function - takes only args, no app param."""
        args = Namespace(resource="provider", action="config")

        from unittest.mock import MagicMock

        from orb.application.dto.interface_response import InterfaceResponse
        from orb.interface.response_formatting_service import ResponseFormattingService

        mock_container = Mock()

        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute = AsyncMock(
            return_value=MagicMock(config={"provider": "aws"}, message="ok")
        )
        formatter = ResponseFormattingService(MagicMock())
        mock_container.get.side_effect = lambda t: (
            formatter if t is ResponseFormattingService else mock_orchestrator
        )

        # handle_provider_config takes only args (no app parameter)
        args._container = mock_container
        result = await handle_provider_config(args)

        assert isinstance(result, InterfaceResponse)
        assert result.data["config"] == {"provider": "aws"}

    @pytest.mark.asyncio
    async def test_handle_validate_provider_config(self):
        """Test handle_validate_provider_config function - takes only args, no app param."""
        args = Namespace(resource="provider", action="validate")

        # handle_validate_provider_config takes only args (no app parameter)
        result = await handle_validate_provider_config(args)

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_handle_reload_provider_config(self):
        """Test handle_reload_provider_config returns InterfaceResponse when reload unsupported."""
        from orb.application.dto.interface_response import InterfaceResponse
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.interface.response_formatting_service import ResponseFormattingService

        args = Namespace(resource="provider", action="reload")

        mock_registry = Mock(spec=ProviderRegistryService)
        # spec=ProviderRegistryService means hasattr(mock_registry, "reload") is False
        # unless ProviderRegistryService has a reload method — formatter.format_error path
        mock_formatter = Mock(spec=ResponseFormattingService)
        mock_formatter.format_error.return_value = InterfaceResponse(
            data={"success": False, "error": "Reload not supported by current provider registry"},
            exit_code=1,
        )
        mock_formatter.format_success.return_value = InterfaceResponse(
            data={"message": "Provider configuration reloaded", "success": True}, exit_code=0
        )

        mock_container = Mock()
        mock_container.get.side_effect = lambda t: (
            mock_formatter if t is ResponseFormattingService else mock_registry
        )

        args._container = mock_container
        result = await handle_reload_provider_config(args)

        assert isinstance(result, InterfaceResponse)


class TestConfigurationHandlerImports:
    """Test that configuration handlers can be imported correctly."""

    def test_import_configuration_handlers(self):
        """Test that all configuration handlers can be imported."""
        from orb.interface.command_handlers import (
            handle_provider_config,
            handle_reload_provider_config,
            handle_validate_provider_config,
        )

        # Verify all handlers are callable functions
        assert callable(handle_provider_config)
        assert callable(handle_validate_provider_config)
        assert callable(handle_reload_provider_config)

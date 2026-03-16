"""Integration tests for bootstrap with configuration-driven providers."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from orb.bootstrap import Application


class TestBootstrapIntegration:
    """Test bootstrap integration with configuration-driven providers."""

    def _make_mock_container(self, mock_config_manager, mock_registry=None):
        """Create a mock DI container that returns mock_config_manager for ConfigurationPort."""
        from orb.domain.base.ports.configuration_port import ConfigurationPort
        from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort

        mock_container = Mock()
        mock_container.is_lazy_loading_enabled.return_value = False

        def _container_get(service_type):
            if service_type is ConfigurationPort:
                return mock_config_manager
            if service_type is ProviderRegistryPort and mock_registry is not None:
                return mock_registry
            return Mock()

        mock_container.get.side_effect = _container_get
        return mock_container

    def _make_mock_config_manager(self, provider_type="aws"):
        """Create a fully configured mock config manager."""
        mock_config_manager = Mock()
        mock_config_manager.get.return_value = {"type": provider_type}
        mock_config_manager.get_provider_config.return_value = None

        mock_app_config = Mock()
        mock_logging_config = Mock()
        mock_logging_config.level = "DEBUG"
        mock_logging_config.format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        mock_logging_config.file_path = "logs/test.log"
        mock_logging_config.max_size = 10485760
        mock_logging_config.backup_count = 5
        mock_logging_config.console_enabled = True
        mock_logging_config.accept_propagated_setting = False
        mock_app_config.logging = mock_logging_config
        mock_config_manager.get_typed.return_value = mock_app_config
        return mock_config_manager

    @pytest.mark.asyncio
    @patch("orb.infrastructure.logging.logger.setup_logging")
    async def test_application_initialization_with_provider_config(self, mock_setup_logging):
        """Test application initialization with integrated provider configuration."""
        mock_config_manager = self._make_mock_config_manager()

        mock_registry = Mock()
        mock_registry.get_registered_providers.return_value = ["aws"]
        mock_registry.get_registered_provider_instances.return_value = ["aws-primary"]
        mock_registry.is_provider_instance_registered.return_value = True

        with (
            patch("orb.infrastructure.di.container.get_container") as mock_get_container,
            patch.object(
                Application,
                "_preload_templates",
                new_callable=lambda: lambda self: AsyncMock(return_value=None)(),
            ),
        ):
            mock_container = self._make_mock_container(mock_config_manager, mock_registry=mock_registry)
            mock_get_container.return_value = mock_container

            app = Application(config_path="/test/config.json", skip_validation=True)
            with patch.object(app, "_preload_templates", new=AsyncMock(return_value=None)):
                result = await app.initialize()

        assert result is True
        assert app._initialized is True

    @pytest.mark.asyncio
    @patch("orb.infrastructure.logging.logger.setup_logging")
    async def test_application_initialization_with_legacy_config(self, mock_setup_logging):
        """Test application initialization with legacy provider configuration."""
        mock_config_manager = self._make_mock_config_manager()
        mock_config_manager.get_provider_config.side_effect = AttributeError("Method not available")
        mock_config_manager.is_provider_strategy_enabled.return_value = False

        mock_registry = Mock()
        mock_registry.get_registered_providers.return_value = ["aws"]
        mock_registry.get_registered_provider_instances.return_value = ["aws"]
        mock_registry.is_provider_instance_registered.return_value = True

        with patch("orb.infrastructure.di.container.get_container") as mock_get_container:
            mock_container = self._make_mock_container(mock_config_manager, mock_registry=mock_registry)
            mock_get_container.return_value = mock_container

            app = Application(config_path="/test/legacy_config.json", skip_validation=True)
            with patch.object(app, "_preload_templates", new=AsyncMock(return_value=None)):
                result = await app.initialize()

        assert result is True
        assert app._initialized is True

    @pytest.mark.asyncio
    async def test_application_initialization_failure(self):
        """Test application initialization failure handling."""
        with patch("orb.infrastructure.di.container.get_container") as mock_get_container:
            mock_container = Mock()
            mock_container.is_lazy_loading_enabled.return_value = False
            mock_container.get.side_effect = Exception("Configuration error")
            mock_get_container.return_value = mock_container

            app = Application(config_path="/invalid/config.json", skip_validation=True)
            result = await app.initialize()

        assert result is False
        assert app._initialized is False

    @pytest.mark.asyncio
    @patch("orb.infrastructure.logging.logger.setup_logging")
    async def test_get_provider_info_integration(self, mock_setup_logging):
        """Test provider info retrieval integration."""
        mock_config_manager = self._make_mock_config_manager()

        mock_registry = Mock()
        mock_registry.get_registered_providers.return_value = ["aws"]
        mock_registry.get_registered_provider_instances.return_value = [
            "aws-primary",
            "aws-backup",
        ]
        mock_registry.is_provider_instance_registered.return_value = True

        with patch("orb.infrastructure.di.container.get_container") as mock_get_container:
            mock_container = self._make_mock_container(mock_config_manager, mock_registry)
            mock_get_container.return_value = mock_container

            app = Application(skip_validation=True)
            with patch.object(app, "_preload_templates", new=AsyncMock(return_value=None)):
                await app.initialize()
            provider_info = app.get_provider_info()

        assert provider_info["status"] == "configured"
        assert provider_info["mode"] == "multi"
        assert provider_info["provider_count"] == 2
        assert "aws-primary" in provider_info["provider_instances"]
        assert "aws-backup" in provider_info["provider_instances"]

    def test_get_provider_info_not_initialized(self):
        """Test provider info retrieval when not initialized."""
        app = Application(skip_validation=True)
        provider_info = app.get_provider_info()

        assert provider_info == {"status": "not_initialized"}

    @pytest.mark.asyncio
    @patch("orb.infrastructure.logging.logger.setup_logging")
    async def test_context_manager_integration(self, mock_setup_logging):
        """Test application context manager integration."""
        mock_config_manager = self._make_mock_config_manager()

        mock_registry = Mock()
        mock_registry.get_registered_providers.return_value = ["aws"]
        mock_registry.get_registered_provider_instances.return_value = ["aws"]
        mock_registry.is_provider_instance_registered.return_value = True

        with patch("orb.infrastructure.di.container.get_container") as mock_get_container:
            mock_container = self._make_mock_container(mock_config_manager, mock_registry=mock_registry)
            mock_get_container.return_value = mock_container

            app = Application(skip_validation=True)
            with patch.object(app, "_preload_templates", new=AsyncMock(return_value=None)):
                async with app:
                    assert app._initialized is True
                    assert hasattr(app, "_provider_registry")

            assert app._initialized is False

    @pytest.mark.asyncio
    async def test_context_manager_initialization_failure(self):
        """Test context manager with initialization failure."""
        with patch("orb.infrastructure.di.container.get_container") as mock_get_container:
            mock_container = Mock()
            mock_container.is_lazy_loading_enabled.return_value = False
            mock_container.get.side_effect = Exception("Initialization failed")
            mock_get_container.return_value = mock_container

            with pytest.raises(RuntimeError, match="Failed to initialize application"):
                async with Application(skip_validation=True):
                    pass

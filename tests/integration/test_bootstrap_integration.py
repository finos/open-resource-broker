"""Integration tests for bootstrap with configuration-driven providers."""

from unittest.mock import Mock, patch

import pytest

from bootstrap import Application


class TestBootstrapIntegration:
    """Test bootstrap integration with configuration-driven providers."""

    def setup_method(self):
        """Set up test fixtures."""
        # Mock the DI container and services
        self.mock_container = Mock()
        self.mock_config_manager = Mock()
        self.mock_application_service = Mock()

    @pytest.mark.asyncio
    @patch("infrastructure.di.services.register_all_services")
    @patch("config.manager.get_config_manager")
    @patch("infrastructure.logging.logger.setup_logging")
    async def test_application_initialization_with_provider_config(
        self, mock_setup_logging, mock_get_config_manager, mock_register_services
    ):
        """Test application initialization with integrated provider configuration."""
        # Setup mocks
        mock_register_services.return_value = None  # register_all_services doesn't return anything
        mock_get_config_manager.return_value = self.mock_config_manager

        # Mock integrated provider configuration - avoid the logging path that causes issues
        self.mock_config_manager.get_provider_config.side_effect = AttributeError(
            "Method not available"
        )
        self.mock_config_manager.get.return_value = {"type": "aws"}

        # Mock AppConfig with complete LoggingConfig attributes
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
        self.mock_config_manager.get_typed.return_value = mock_app_config

        # Mock container and provider context
        mock_provider_context = Mock()
        mock_provider_context.initialize.return_value = True
        mock_provider_context.available_strategies = ["aws-primary"]
        mock_provider_context.current_strategy_type = "aws-primary"

        with (
            patch("infrastructure.di.container.get_container") as mock_get_container,
            patch.object(Application, "_preload_templates") as mock_preload,
        ):
            mock_get_container.return_value = self.mock_container
            self.mock_container.get.return_value = mock_provider_context
            self.mock_container.is_lazy_loading_enabled.return_value = False
            mock_preload.return_value = None  # Mock the async preload method

            # Execute
            app = Application(config_path="/test/config.json")
            result = await app.initialize()

        # Verify
        assert result is True
        assert app._initialized is True

        # Verify configuration logging was attempted
        self.mock_config_manager.get_provider_config.assert_called()

        # Verify provider context initialization
        mock_provider_context.initialize.assert_called_once()

    @pytest.mark.asyncio
    @patch("infrastructure.di.services.register_all_services")
    @patch("config.manager.get_config_manager")
    @patch("infrastructure.logging.logger.setup_logging")
    async def test_application_initialization_with_legacy_config(
        self, mock_setup_logging, mock_get_config_manager, mock_register_services
    ):
        """Test application initialization with legacy provider configuration."""
        # Setup mocks
        mock_register_services.return_value = None
        mock_get_config_manager.return_value = self.mock_config_manager

        # Mock legacy configuration (no integrated config available)
        self.mock_config_manager.get_provider_config.side_effect = AttributeError(
            "Method not available"
        )
        self.mock_config_manager.is_provider_strategy_enabled.return_value = False
        self.mock_config_manager.get.return_value = {"type": "aws"}

        # Mock AppConfig with complete LoggingConfig attributes
        mock_app_config = Mock()
        mock_logging_config = Mock()
        mock_logging_config.level = "INFO"
        mock_logging_config.format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        mock_logging_config.file_path = "logs/test.log"
        mock_logging_config.max_size = 10485760
        mock_logging_config.backup_count = 5
        mock_logging_config.console_enabled = True
        mock_logging_config.accept_propagated_setting = False
        mock_app_config.logging = mock_logging_config
        self.mock_config_manager.get_typed.return_value = mock_app_config

        # Mock container and provider context
        mock_provider_context = Mock()
        mock_provider_context.initialize.return_value = True
        mock_provider_context.available_strategies = ["aws"]
        mock_provider_context.current_strategy_type = "aws"

        with patch("infrastructure.di.container.get_container") as mock_get_container:
            mock_get_container.return_value = self.mock_container
            self.mock_container.get.return_value = mock_provider_context
            self.mock_container.is_lazy_loading_enabled.return_value = False

            # Execute
            app = Application(config_path="/test/legacy_config.json")
            result = await app.initialize()

        # Verify
        assert result is True
        assert app._initialized is True

        # Verify fallback to legacy logging
        mock_provider_context.initialize.assert_called_once()

    @pytest.mark.asyncio
    @patch("infrastructure.di.services.register_all_services")
    @patch("config.manager.get_config_manager")
    async def test_application_initialization_failure(
        self, mock_get_config_manager, mock_register_services
    ):
        """Test application initialization failure handling."""
        # Setup mocks
        mock_register_services.return_value = None
        mock_get_config_manager.return_value = self.mock_config_manager

        # Mock configuration failure
        self.mock_config_manager.get.side_effect = Exception("Configuration error")

        # Execute
        app = Application(config_path="/invalid/config.json")
        result = await app.initialize()

        # Verify
        assert result is False
        assert app._initialized is False

    @pytest.mark.asyncio
    @patch("infrastructure.di.services.register_all_services")
    @patch("config.manager.get_config_manager")
    @patch("infrastructure.logging.logger.setup_logging")
    async def test_get_provider_info_integration(
        self, mock_setup_logging, mock_get_config_manager, mock_register_services
    ):
        """Test provider info retrieval integration."""
        # Setup mocks
        mock_register_services.return_value = None
        mock_get_config_manager.return_value = self.mock_config_manager

        self.mock_config_manager.get.return_value = {"type": "aws"}

        # Mock AppConfig with complete LoggingConfig attributes
        mock_app_config = Mock()
        mock_logging_config = Mock()
        mock_logging_config.level = "INFO"
        mock_logging_config.format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        mock_logging_config.file_path = "logs/test.log"
        mock_logging_config.max_size = 10485760
        mock_logging_config.backup_count = 5
        mock_logging_config.console_enabled = True
        mock_logging_config.accept_propagated_setting = False
        mock_app_config.logging = mock_logging_config
        self.mock_config_manager.get_typed.return_value = mock_app_config

        # Mock provider context with provider info
        mock_provider_context = Mock()
        mock_provider_context.initialize.return_value = True
        mock_provider_context.available_strategies = ["aws-primary", "aws-backup"]
        mock_provider_context.current_strategy_type = "aws-primary"

        with patch("infrastructure.di.container.get_container") as mock_get_container:
            mock_get_container.return_value = self.mock_container
            self.mock_container.get.return_value = mock_provider_context
            self.mock_container.is_lazy_loading_enabled.return_value = False

            # Execute
            app = Application()
            await app.initialize()
            provider_info = app.get_provider_info()

        # Verify
        assert provider_info["status"] == "configured"
        assert provider_info["mode"] == "multi"
        assert provider_info["provider_count"] == 2
        assert provider_info["available_strategies"] == ["aws-primary", "aws-backup"]

    def test_get_provider_info_not_initialized(self):
        """Test provider info retrieval when not initialized."""
        # Execute
        app = Application()
        provider_info = app.get_provider_info()

        # Verify
        assert provider_info == {"status": "not_initialized"}

    @pytest.mark.asyncio
    @patch("infrastructure.di.services.register_all_services")
    @patch("config.manager.get_config_manager")
    @patch("infrastructure.logging.logger.setup_logging")
    async def test_context_manager_integration(
        self, mock_setup_logging, mock_get_config_manager, mock_register_services
    ):
        """Test application context manager integration."""
        # Setup mocks
        mock_register_services.return_value = None
        mock_get_config_manager.return_value = self.mock_config_manager

        self.mock_config_manager.get.return_value = {"type": "aws"}
        self.mock_config_manager.get_provider_config.side_effect = AttributeError(
            "Method not available"
        )

        # Mock AppConfig with complete LoggingConfig attributes
        mock_app_config = Mock()
        mock_logging_config = Mock()
        mock_logging_config.level = "INFO"
        mock_logging_config.format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        mock_logging_config.file_path = "logs/test.log"
        mock_logging_config.max_size = 10485760
        mock_logging_config.backup_count = 5
        mock_logging_config.console_enabled = True
        mock_logging_config.accept_propagated_setting = False
        mock_app_config.logging = mock_logging_config
        self.mock_config_manager.get_typed.return_value = mock_app_config

        # Mock provider context
        mock_provider_context = Mock()
        mock_provider_context.initialize.return_value = True
        mock_provider_context.available_strategies = ["aws"]
        mock_provider_context.current_strategy_type = "aws"

        with (
            patch("infrastructure.di.container.get_container") as mock_get_container,
            patch.object(Application, "_preload_templates") as mock_preload,
        ):
            mock_get_container.return_value = self.mock_container
            self.mock_container.get.return_value = mock_provider_context
            self.mock_container.is_lazy_loading_enabled.return_value = False
            mock_preload.return_value = None

            # Execute
            async with Application() as app:
                assert app._initialized is True
                # Test that we can access provider context through the app
                assert hasattr(app, "_provider_context")

            # Verify shutdown was called
            assert app._initialized is False

    @pytest.mark.asyncio
    @patch("infrastructure.di.services.register_all_services")
    @patch("config.manager.get_config_manager")
    async def test_context_manager_initialization_failure(
        self, mock_get_config_manager, mock_register_services
    ):
        """Test context manager with initialization failure."""
        # Setup mocks
        mock_register_services.return_value = None
        mock_get_config_manager.return_value = self.mock_config_manager

        # Mock initialization failure
        self.mock_config_manager.get.side_effect = Exception("Initialization failed")

        # Execute & Verify
        with pytest.raises(RuntimeError, match="Failed to initialize application"):
            async with Application():
                pass

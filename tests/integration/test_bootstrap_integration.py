"""Integration tests for bootstrap with configuration-driven providers."""

from unittest.mock import Mock, patch

import pytest

from src.bootstrap import Application


class TestBootstrapIntegration:
    """Test bootstrap integration with configuration-driven providers."""

    def setup_method(self):
        """Set up test fixtures."""
        # Mock the DI container and services
        self.mock_container = Mock()
        self.mock_config_manager = Mock()
        self.mock_application_service = Mock()

    @patch("src.bootstrap.register_services")
    @patch("src.bootstrap.get_config_manager")
    @patch("src.bootstrap.setup_logging")
    def test_application_initialization_with_provider_config(
        self, mock_setup_logging, mock_get_config_manager, mock_register_services
    ):
        """Test application initialization with unified provider configuration."""
        # Setup mocks
        mock_register_services.return_value = self.mock_container
        mock_get_config_manager.return_value = self.mock_config_manager

        # Mock unified provider configuration
        from src.config.schemas.provider_strategy_schema import (
            ProviderConfig,
            ProviderInstanceConfig,
        )

        provider_config = ProviderConfig(
            selection_policy="ROUND_ROBIN",
            providers=[
                ProviderInstanceConfig(name="aws-primary", type="aws", enabled=True),
                ProviderInstanceConfig(name="aws-backup", type="aws", enabled=False),
            ],
        )

        self.mock_config_manager.get_provider_config.return_value = provider_config
        self.mock_config_manager.get.return_value = {"type": "aws"}

        # Mock AppConfig
        mock_app_config = Mock()
        mock_app_config.logging = Mock()
        self.mock_config_manager.get_typed.return_value = mock_app_config

        # Mock ApplicationService
        self.mock_application_service.initialize.return_value = True
        self.mock_application_service.get_provider_info.return_value = {
            "mode": "single",
            "provider_names": ["aws-primary"],
        }
        self.mock_container.get.return_value = self.mock_application_service

        # Execute
        _ = Application(config_path="/test/config.json")
        result = app.initialize()

        # Verify
        assert result is True
        assert app._initialized is True

        # Verify configuration logging was attempted
        self.mock_config_manager.get_provider_config.assert_called()

        # Verify application service initialization
        self.mock_application_service.initialize.assert_called_once()
        self.mock_application_service.get_provider_info.assert_called_once()

    @patch("src.bootstrap.register_services")
    @patch("src.bootstrap.get_config_manager")
    @patch("src.bootstrap.setup_logging")
    def test_application_initialization_with_legacy_config(
        self, mock_setup_logging, mock_get_config_manager, mock_register_services
    ):
        """Test application initialization with legacy provider configuration."""
        # Setup mocks
        mock_register_services.return_value = self.mock_container
        mock_get_config_manager.return_value = self.mock_config_manager

        # Mock legacy configuration (no unified config available)
        self.mock_config_manager.get_provider_config.side_effect = AttributeError(
            "Method not available"
        )
        self.mock_config_manager.is_provider_strategy_enabled.return_value = False
        self.mock_config_manager.get.return_value = {"type": "aws"}

        # Mock AppConfig
        mock_app_config = Mock()
        mock_app_config.logging = Mock()
        self.mock_config_manager.get_typed.return_value = mock_app_config

        # Mock ApplicationService
        self.mock_application_service.initialize.return_value = True
        self.mock_application_service.get_provider_info.return_value = {
            "mode": "legacy",
            "provider_type": "aws",
        }
        self.mock_container.get.return_value = self.mock_application_service

        # Execute
        _ = Application(config_path="/test/legacy_config.json")
        result = app.initialize()

        # Verify
        assert result is True
        assert app._initialized is True

        # Verify fallback to legacy logging
        self.mock_application_service.initialize.assert_called_once()

    @patch("src.bootstrap.register_services")
    @patch("src.bootstrap.get_config_manager")
    def test_application_initialization_failure(
        self, mock_get_config_manager, mock_register_services
    ):
        """Test application initialization failure handling."""
        # Setup mocks
        mock_register_services.return_value = self.mock_container
        mock_get_config_manager.return_value = self.mock_config_manager

        # Mock configuration failure
        self.mock_config_manager.get.side_effect = Exception("Configuration error")

        # Execute
        _ = Application(config_path="/invalid/config.json")
        result = app.initialize()

        # Verify
        assert result is False
        assert app._initialized is False

    @patch("src.bootstrap.register_services")
    @patch("src.bootstrap.get_config_manager")
    @patch("src.bootstrap.setup_logging")
    def test_get_provider_info_integration(
        self, mock_setup_logging, mock_get_config_manager, mock_register_services
    ):
        """Test provider info retrieval integration."""
        # Setup mocks
        mock_register_services.return_value = self.mock_container
        mock_get_config_manager.return_value = self.mock_config_manager

        self.mock_config_manager.get.return_value = {"type": "aws"}

        # Mock AppConfig
        mock_app_config = Mock()
        mock_app_config.logging = Mock()
        self.mock_config_manager.get_typed.return_value = mock_app_config

        # Mock ApplicationService with provider info
        expected_provider_info = {
            "mode": "strategy",
            "selection_policy": "ROUND_ROBIN",
            "active_providers": 2,
            "provider_names": ["aws-primary", "aws-backup"],
        }

        self.mock_application_service.initialize.return_value = True
        self.mock_application_service.get_provider_info.return_value = (
            expected_provider_info
        )
        self.mock_container.get.return_value = self.mock_application_service

        # Execute
        _ = Application()
        app.initialize()
        provider_info = app.get_provider_info()

        # Verify
        assert provider_info == expected_provider_info
        assert provider_info["mode"] == "strategy"
        assert provider_info["active_providers"] == 2

    def test_get_provider_info_not_initialized(self):
        """Test provider info retrieval when not initialized."""
        # Execute
        _ = Application()
        provider_info = app.get_provider_info()

        # Verify
        assert provider_info == {"status": "not_initialized"}

    @patch("src.bootstrap.register_services")
    @patch("src.bootstrap.get_config_manager")
    @patch("src.bootstrap.setup_logging")
    def test_context_manager_integration(
        self, mock_setup_logging, mock_get_config_manager, mock_register_services
    ):
        """Test application context manager integration."""
        # Setup mocks
        mock_register_services.return_value = self.mock_container
        mock_get_config_manager.return_value = self.mock_config_manager

        self.mock_config_manager.get.return_value = {"type": "aws"}

        # Mock AppConfig
        mock_app_config = Mock()
        mock_app_config.logging = Mock()
        self.mock_config_manager.get_typed.return_value = mock_app_config

        # Mock ApplicationService
        self.mock_application_service.initialize.return_value = True
        self.mock_container.get.return_value = self.mock_application_service

        # Execute
        with Application() as app:
            assert app._initialized is True
            assert app.get_application_service() == self.mock_application_service

        # Verify shutdown was called
        assert app._initialized is False

    @patch("src.bootstrap.register_services")
    @patch("src.bootstrap.get_config_manager")
    def test_context_manager_initialization_failure(
        self, mock_get_config_manager, mock_register_services
    ):
        """Test context manager with initialization failure."""
        # Setup mocks
        mock_register_services.return_value = self.mock_container
        mock_get_config_manager.return_value = self.mock_config_manager

        # Mock initialization failure
        self.mock_config_manager.get.side_effect = Exception("Initialization failed")

        # Execute & Verify
        with pytest.raises(RuntimeError, match="Failed to initialize application"):
            with Application() as app:
                pass

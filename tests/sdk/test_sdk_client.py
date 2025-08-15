"""Unit tests for SDK client following existing test patterns."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from sdk.client import OpenHFPluginSDK
from sdk.config import SDKConfig
from sdk.exceptions import ConfigurationError, ProviderError, SDKError


class TestOpenHFPluginSDK:
    """Test cases for OpenHFPluginSDK following existing test patterns."""

    def test_sdk_initialization_with_defaults(self):
        """Test SDK initialization with default configuration."""
        sdk = OpenHFPluginSDK()

        assert sdk.provider == "aws"
        assert not sdk.initialized
        assert isinstance(sdk.config, SDKConfig)

    def test_sdk_initialization_with_custom_provider(self):
        """Test SDK initialization with custom provider."""
        sdk = OpenHFPluginSDK(provider="mock")

        assert sdk.provider == "mock"
        assert not sdk.initialized

    def test_sdk_initialization_with_config_dict(self):
        """Test SDK initialization with configuration dictionary."""
        config = {"provider": "mock", "timeout": 600, "log_level": "DEBUG"}

        sdk = OpenHFPluginSDK(config=config)

        assert sdk.provider == "mock"
        assert sdk.config.timeout == 600
        assert sdk.config.log_level == "DEBUG"

    def test_sdk_initialization_with_kwargs(self):
        """Test SDK initialization with additional kwargs."""
        sdk = OpenHFPluginSDK(provider="mock", custom_option="test_value")

        assert sdk.provider == "mock"
        assert sdk.config.custom_config["custom_option"] == "test_value"

    @pytest.mark.asyncio
    async def test_sdk_context_manager_success(self):
        """Test SDK as async context manager with successful initialization."""
        with patch.object(OpenHFPluginSDK, "initialize", new_callable=AsyncMock) as mock_init:
            with patch.object(OpenHFPluginSDK, "cleanup", new_callable=AsyncMock) as mock_cleanup:
                mock_init.return_value = True

                async with OpenHFPluginSDK(provider="mock") as sdk:
                    assert sdk is not None

                mock_init.assert_called_once()
                mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_sdk_context_manager_with_exception(self):
        """Test SDK context manager cleanup on exception."""
        with patch.object(OpenHFPluginSDK, "initialize", new_callable=AsyncMock) as mock_init:
            with patch.object(OpenHFPluginSDK, "cleanup", new_callable=AsyncMock) as mock_cleanup:
                mock_init.return_value = True

                with pytest.raises(ValueError):
                    async with OpenHFPluginSDK(provider="mock") as sdk:
                        raise ValueError("Test exception")

                mock_init.assert_called_once()
                mock_cleanup.assert_called_once()

    def test_list_available_methods_not_initialized(self):
        """Test list_available_methods raises error when not initialized."""
        sdk = OpenHFPluginSDK(provider="mock")

        with pytest.raises(SDKError, match="SDK not initialized"):
            sdk.list_available_methods()

    def test_get_method_info_not_initialized(self):
        """Test get_method_info raises error when not initialized."""
        sdk = OpenHFPluginSDK(provider="mock")

        with pytest.raises(SDKError, match="SDK not initialized"):
            sdk.get_method_info("test_method")

    def test_get_methods_by_type_not_initialized(self):
        """Test get_methods_by_type raises error when not initialized."""
        sdk = OpenHFPluginSDK(provider="mock")

        with pytest.raises(SDKError, match="SDK not initialized"):
            sdk.get_methods_by_type("query")

    def test_sdk_stats_not_initialized(self):
        """Test get_stats returns appropriate info when not initialized."""
        sdk = OpenHFPluginSDK(provider="mock")

        stats = sdk.get_stats()

        assert stats["initialized"] is False
        assert stats["provider"] == "mock"
        assert stats["methods_discovered"] == 0

    def test_sdk_repr(self):
        """Test SDK string representation."""
        sdk = OpenHFPluginSDK(provider="mock")

        repr_str = repr(sdk)

        assert "OpenHFPluginSDK" in repr_str
        assert "provider='mock'" in repr_str
        assert "not initialized" in repr_str
        assert "methods=0" in repr_str

    @pytest.mark.asyncio
    async def test_initialize_application_failure(self):
        """Test initialization failure when application fails to initialize."""
        with patch("src.sdk.client.Application") as mock_app_class:
            mock_app = Mock()
            mock_app.initialize.return_value = False
            mock_app_class.return_value = mock_app

            sdk = OpenHFPluginSDK(provider="mock")

            with pytest.raises(ProviderError, match="Failed to initialize mock provider"):
                await sdk.initialize()

    @pytest.mark.asyncio
    async def test_initialize_missing_application_service(self):
        """Test initialization failure when application service is not available."""
        with patch("src.sdk.client.Application") as mock_app_class:
            mock_app = Mock()
            mock_app.initialize.return_value = True
            mock_app._application_service = None
            mock_app_class.return_value = mock_app

            sdk = OpenHFPluginSDK(provider="mock")

            with pytest.raises(ConfigurationError, match="Application service not available"):
                await sdk.initialize()

    @pytest.mark.asyncio
    async def test_cleanup_with_exception(self):
        """Test cleanup handles exceptions gracefully."""
        sdk = OpenHFPluginSDK(provider="mock")
        sdk._initialized = True

        # Mock app with cleanup that raises exception
        mock_app = Mock()
        mock_app.cleanup = AsyncMock(side_effect=Exception("Cleanup error"))
        sdk._app = mock_app

        # Should not raise exception
        await sdk.cleanup()

        assert not sdk.initialized

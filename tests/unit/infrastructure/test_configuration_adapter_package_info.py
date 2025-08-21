"""Tests for ConfigurationAdapter get_package_info method."""

from unittest.mock import Mock, patch

from infrastructure.adapters.configuration_adapter import ConfigurationAdapter


class TestConfigurationAdapterPackageInfo:
    """Test ConfigurationAdapter package info functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config_manager = Mock()
        self.adapter = ConfigurationAdapter(self.mock_config_manager)

    @patch("infrastructure.adapters.configuration_adapter._package")
    def test_get_package_info_success(self, mock_package):
        """Test successful package info retrieval."""
        # Arrange
        mock_package.PACKAGE_NAME = "open-hostfactory-plugin"
        mock_package.__version__ = "1.0.0"
        mock_package.DESCRIPTION = "Test description"
        mock_package.AUTHOR = "Test Author"

        # Act
        result = self.adapter.get_package_info()

        # Assert
        assert result == {
            "name": "open-hostfactory-plugin",
            "version": "1.0.0",
            "description": "Test description",
            "author": "Test Author",
        }

    def test_get_package_info_import_error_fallback(self):
        """Test fallback when package import fails."""
        # Act - import will fail naturally in test environment
        result = self.adapter.get_package_info()

        # Assert - should return fallback values
        assert result["name"] == "open-hostfactory-plugin"
        assert result["version"] == "unknown"
        assert result["description"] == "Cloud provider integration plugin"
        assert result["author"] == "AWS Professional Services"

    @patch("infrastructure.adapters.configuration_adapter._package")
    def test_get_package_info_partial_data(self, mock_package):
        """Test package info with missing attributes."""
        # Arrange - only set some attributes
        mock_package.PACKAGE_NAME = "test-package"
        mock_package.__version__ = "2.0.0"
        # Missing DESCRIPTION and AUTHOR

        # Act
        result = self.adapter.get_package_info()

        # Assert - should handle missing attributes gracefully
        assert result["name"] == "test-package"
        assert result["version"] == "2.0.0"
        assert "description" in result
        assert "author" in result

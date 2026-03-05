"""Tests for ConfigurationAdapter get_package_info method."""

from unittest.mock import Mock, patch

import pytest

from infrastructure.adapters.configuration_adapter import ConfigurationAdapter


class TestConfigurationAdapterPackageInfo:
    """Test ConfigurationAdapter package info functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config_manager = Mock()
        self.adapter = ConfigurationAdapter(self.mock_config_manager)

    @patch("infrastructure.adapters.configuration_adapter._package", create=True)
    def test_get_package_info_success(self, mock_package):
        """Test successful package info retrieval."""
        # Arrange - patch the _package module-level name used inside get_package_info
        mock_package.PACKAGE_NAME = "open-resource-broker"
        mock_package.__version__ = "1.0.0"
        mock_package.DESCRIPTION = "Test description"
        mock_package.AUTHOR = "Test Author"

        # The method does `from _package import ...` so we patch at the import site
        with patch.dict(
            "sys.modules",
            {
                "_package": type(
                    "FakePackage",
                    (),
                    {
                        "PACKAGE_NAME": "open-resource-broker",
                        "__version__": "1.0.0",
                        "DESCRIPTION": "Test description",
                        "AUTHOR": "Test Author",
                    },
                )()
            },
        ):
            result = self.adapter.get_package_info()

        assert result == {
            "name": "open-resource-broker",
            "version": "1.0.0",
            "description": "Test description",
            "author": "Test Author",
        }

    def test_get_package_info_import_error_fallback(self):
        """Test that ImportError is raised when _package module is not available."""
        from unittest.mock import patch

        # Setting sys.modules["_package"] = None forces ImportError on `from _package import ...`
        with patch.dict("sys.modules", {"_package": None}):
            adapter = ConfigurationAdapter(self.mock_config_manager)
            with pytest.raises(ImportError):
                adapter.get_package_info()

    def test_get_package_info_partial_data(self):
        """Test package info with all required attributes present."""
        import types

        fake_pkg = types.ModuleType("_package")
        fake_pkg.PACKAGE_NAME = "test-package"
        fake_pkg.__version__ = "2.0.0"
        fake_pkg.DESCRIPTION = "A description"
        fake_pkg.AUTHOR = "An author"

        with patch.dict("sys.modules", {"_package": fake_pkg}):
            result = self.adapter.get_package_info()

        assert result["name"] == "test-package"
        assert result["version"] == "2.0.0"
        assert "description" in result
        assert "author" in result

"""Integration tests for package name functionality."""

from unittest.mock import Mock, patch

from domain.base.ports.configuration_port import ConfigurationPort
from infrastructure.di.container import DIContainer
from providers.aws.infrastructure.services.aws_native_spec_service import (
    AWSNativeSpecService,
)


class TestPackageNameIntegration:
    """Integration tests for package name usage across components."""

    def setup_method(self):
        """Set up test fixtures."""
        self.container = DIContainer()

    @patch("infrastructure.adapters.configuration_adapter._package")
    def test_end_to_end_package_name_flow(self, mock_package):
        """Test complete package name flow from configuration to template rendering."""
        # Arrange
        mock_package.PACKAGE_NAME = "test-plugin"
        mock_package.__version__ = "2.0.0"
        mock_package.DESCRIPTION = "Test plugin"
        mock_package.AUTHOR = "Test Author"

        # Get configuration port from container
        config_port = self.container.get(ConfigurationPort)

        # Act - get package info
        package_info = config_port.get_package_info()

        # Assert - package info is correctly retrieved
        assert package_info["name"] == "test-plugin"
        assert package_info["version"] == "2.0.0"

    def test_package_name_fallback_integration(self):
        """Test that fallback works across the entire system."""
        # Arrange - no mocking, let import fail naturally
        config_port = self.container.get(ConfigurationPort)

        # Act
        package_info = config_port.get_package_info()

        # Assert - should get fallback values
        assert package_info["name"] == "open-hostfactory-plugin"
        assert package_info["version"] == "unknown"
        assert "description" in package_info
        assert "author" in package_info

    @patch("infrastructure.adapters.configuration_adapter._package")
    def test_native_spec_service_uses_package_info(self, mock_package):
        """Test that native spec service correctly uses package info in context."""
        # Arrange
        mock_package.PACKAGE_NAME = "integration-test-plugin"
        mock_package.__version__ = "3.0.0"

        config_port = self.container.get(ConfigurationPort)

        # Mock native spec service
        mock_native_spec = Mock()
        aws_native_spec = AWSNativeSpecService(
            config_port=config_port, native_spec_service=mock_native_spec
        )

        # Create test template and request
        from domain.request.request import Request
        from domain.request.value_objects import RequestId
        from providers.aws.domain.template import AWSTemplate

        template = AWSTemplate(
            template_id="integration-test", image_id="ami-test", instance_type="t3.micro"
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=1, template_id="integration-test"
        )

        # Act
        context = aws_native_spec._build_aws_context(template, request)

        # Assert
        assert context["package_name"] == "integration-test-plugin"
        assert context["package_version"] == "3.0.0"

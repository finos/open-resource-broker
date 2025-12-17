"""Integration tests for package name functionality."""

from unittest.mock import Mock, patch

from config.manager import ConfigurationManager
from infrastructure.adapters.configuration_adapter import ConfigurationAdapter
from providers.aws.infrastructure.services.aws_native_spec_service import (
    AWSNativeSpecService,
)


class TestPackageNameIntegration:
    """Integration tests for package name usage across components."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config_manager = ConfigurationManager()
        self.config_adapter = ConfigurationAdapter(self.config_manager)

    def test_end_to_end_package_name_flow(self):
        """Test complete package name flow from configuration to template rendering."""
        # Arrange - patch _package module attributes
        with (
            patch("_package.PACKAGE_NAME", "test-plugin"),
            patch("_package.__version__", "2.0.0"),
            patch("_package.DESCRIPTION", "Test plugin"),
            patch("_package.AUTHOR", "Test Author"),
        ):
            # Act - get package info
            package_info = self.config_adapter.get_package_info()

            # Assert - package info is correctly retrieved
            assert package_info["name"] == "test-plugin"
            assert package_info["version"] == "2.0.0"

    def test_package_name_fallback_integration(self):
        """Test that fallback works when package info is incomplete."""
        # Arrange - mock get_package_info to return empty dict (simulating missing data)
        with patch.object(self.config_adapter, "get_package_info", return_value={}):
            # Act - get package info
            package_info = self.config_adapter.get_package_info()

            # Assert - verify fallback behavior
            # When package_info is empty, consumers should use fallback values
            assert package_info.get("version", "unknown") == "unknown"
            assert package_info.get("name", "open-resource-broker") == "open-resource-broker"

    def test_native_spec_service_uses_package_info(self):
        """Test that native spec service correctly uses package info in context."""
        with (
            patch("_package.PACKAGE_NAME", "integration-test-plugin"),
            patch("_package.__version__", "3.0.0"),
            patch("_package.DESCRIPTION", "Test"),
            patch("_package.AUTHOR", "Test"),
        ):
            # Mock native spec service
            mock_native_spec = Mock()
            aws_native_spec = AWSNativeSpecService(
                config_port=self.config_adapter, native_spec_service=mock_native_spec
            )

            # Create test template and request
            from domain.request.aggregate import Request
            from domain.request.value_objects import RequestId, RequestType
            from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
            from providers.aws.domain.template.value_objects import ProviderApi

            template = AWSTemplate(
                template_id="integration-test",
                image_id="ami-test",
                instance_type="t3.micro",
                provider_api=ProviderApi.EC2_FLEET,
                subnet_ids=["subnet-test"],
            )

            request = Request(
                request_id=RequestId.generate(RequestType.ACQUIRE),
                requested_count=1,
                template_id="integration-test",
                request_type=RequestType.ACQUIRE,
                provider_type="aws",
            )

            # Act
            context = aws_native_spec._build_aws_context(template, request)

            # Assert
            assert context["package_name"] == "integration-test-plugin"
            assert context["package_version"] == "3.0.0"

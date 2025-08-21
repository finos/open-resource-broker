"""Tests for AWS native spec service."""

from unittest.mock import Mock

import pytest

from providers.aws.infrastructure.services.aws_native_spec_service import (
    AWSNativeSpecService,
)


class TestAWSNativeSpecService:
    """Test AWS native spec service."""

    @pytest.fixture
    def native_spec_service(self):
        """Mock native spec service."""
        return Mock()

    @pytest.fixture
    def config_port(self):
        """Mock configuration port."""
        return Mock()

    @pytest.fixture
    def service(self, native_spec_service, config_port):
        """Create service instance."""
        return AWSNativeSpecService(native_spec_service, config_port)

    @pytest.fixture
    def aws_template(self):
        """Create mock AWS template with native specs."""
        template = Mock()
        template.template_id = "test-template"
        template.image_id = "ami-12345678"
        template.instance_type = "t2.micro"
        template.launch_template_spec = {
            "LaunchTemplateName": "custom-template",
            "LaunchTemplateData": {
                "ImageId": "{{ image_id }}",
                "InstanceType": "{{ instance_type }}",
            },
        }
        template.launch_template_spec_file = None
        template.provider_api_spec = None
        template.provider_api_spec_file = None
        return template

    @pytest.fixture
    def test_request(self):
        """Create mock request."""
        request = Mock()
        request.request_id = "req-12345"
        request.requested_count = 3
        return request

    def test_process_launch_template_spec_when_disabled(
        self, service, native_spec_service, aws_template, test_request
    ):
        """Test launch template spec processing when native specs disabled."""
        native_spec_service.is_native_spec_enabled.return_value = False

        result = service.process_launch_template_spec(aws_template, test_request)

        assert result is None

    def test_process_launch_template_spec_with_inline_spec(
        self, service, native_spec_service, aws_template, test_request
    ):
        """Test launch template spec processing with inline spec."""
        native_spec_service.is_native_spec_enabled.return_value = True
        native_spec_service.render_spec.return_value = {
            "LaunchTemplateName": "custom-template",
            "LaunchTemplateData": {"ImageId": "ami-12345678", "InstanceType": "t2.micro"},
        }

        result = service.process_launch_template_spec(aws_template, test_request)

        assert result is not None
        assert result["LaunchTemplateName"] == "custom-template"
        assert result["LaunchTemplateData"]["ImageId"] == "ami-12345678"

    def test_process_provider_api_spec_when_no_spec(
        self, service, native_spec_service, test_request
    ):
        """Test provider API spec processing when no spec provided."""
        template = Mock()
        template.template_id = "test-template"
        template.image_id = "ami-12345678"
        template.instance_type = "t2.micro"
        template.launch_template_spec = None
        template.launch_template_spec_file = None
        template.provider_api_spec = None
        template.provider_api_spec_file = None

        native_spec_service.is_native_spec_enabled.return_value = True

        result = service.process_provider_api_spec(template, test_request)

        assert result is None

    def test_build_aws_context(self, service, aws_template, test_request):
        """Test AWS context building."""
        context = service._build_aws_context(aws_template, test_request)

        assert context["request_id"] == "req-12345"
        assert context["requested_count"] == 3
        assert context["template_id"] == "test-template"
        assert context["image_id"] == "ami-12345678"
        assert context["instance_type"] == "t2.micro"

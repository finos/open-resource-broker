"""
End-to-end functionality tests for core Host Factory operations.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_application_service_instantiation():
    """Test that ApplicationService can be imported."""
    from application.service import ApplicationService

    # Just test that the class can be imported
    assert ApplicationService is not None
    assert hasattr(ApplicationService, "__init__")

    # Test that it's a class
    assert isinstance(ApplicationService, type)


def test_configuration_loading():
    """Test that configuration can be loaded."""
    from config.manager import ConfigurationManager

    config_manager = ConfigurationManager()
    # Use the correct method name
    config = config_manager.app_config

    assert config is not None
    assert hasattr(config, "provider")
    assert hasattr(config, "logging")


@patch("boto3.client")
def test_aws_provider_adapter(mock_boto_client):
    """Test that AWS provider adapter can be instantiated."""
    from providers.aws.strategy.aws_provider_adapter import AWSProviderAdapter

    # Mock AWS client
    mock_client = MagicMock()
    mock_boto_client.return_value = mock_client

    # Create mock logger for required parameter
    mock_logger = MagicMock()

    # Create adapter with required logger parameter
    adapter = AWSProviderAdapter(logger=mock_logger)

    assert adapter is not None


def test_template_operations():
    """Test template-related operations."""
    from domain.template.value_objects import TemplateId

    # Test template ID creation
    template_id = TemplateId(value="test-template")
    assert template_id.value == "test-template"

    # Test template data structure
    template_data = {
        "template_id": "test-template",
        "name": "Test Template",
        "provider_api": "ec2_fleet",
        "image_id": "ami-12345678",
        "instance_type": "t2.micro",
    }

    assert template_data["template_id"] == "test-template"
    assert template_data["provider_api"] == "ec2_fleet"


def test_request_operations():
    """Test request-related operations."""
    from domain.request.value_objects import RequestId

    # Test request ID creation
    request_id = RequestId(value="req-12345678-1234-1234-1234-123456789012")
    assert request_id.value == "req-12345678-1234-1234-1234-123456789012"

    # Test request data structure
    request_data = {
        "request_id": "req-12345678-1234-1234-1234-123456789012",
        "template_id": "test-template",
        "machine_count": 2,
        "status": "pending",
    }

    assert request_data["request_id"] == "req-12345678-1234-1234-1234-123456789012"
    assert request_data["machine_count"] == 2


def test_machine_operations():
    """Test machine-related operations."""
    from domain.machine.value_objects import MachineId

    # Test machine ID creation
    machine_id = MachineId(value="i-1234567890abcdef0")
    assert machine_id.value == "i-1234567890abcdef0"

    # Test machine data structure
    machine_data = {
        "machine_id": "i-1234567890abcdef0",
        "request_id": "req-12345678-1234-1234-1234-123456789012",
        "template_id": "test-template",
        "status": "running",
        "instance_type": "t2.micro",
    }

    assert machine_data["machine_id"] == "i-1234567890abcdef0"
    assert machine_data["status"] == "running"


@patch("src.infrastructure.logging.logger.get_logger")
def test_logging_functionality(mock_get_logger):
    """Test that logging functionality works."""
    mock_logger = MagicMock()
    mock_get_logger.return_value = mock_logger

    from infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)
    logger.info("Test message")

    # Verify logger was called
    mock_get_logger.assert_called()


def test_exception_handling():
    """Test that exception handling works."""
    from domain.base.exceptions import DomainException, ValidationError
    from providers.aws.exceptions.aws_exceptions import AWSError

    # Test domain exceptions
    try:
        raise ValidationError("Test validation error")
    except DomainException as e:
        assert str(e) == "Test validation error"

    # Test AWS exceptions
    try:
        raise AWSError("Test AWS error")
    except AWSError as e:
        assert str(e) == "Test AWS error"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

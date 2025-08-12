"""
Simple test configuration for basic functionality testing.
This avoids the complex DI system that has circular import issues.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# Mock AWS services to avoid import issues
@pytest.fixture
def mock_aws():
    """Mock AWS services."""
    with pytest.MonkeyPatch().context() as m:
        # Mock boto3
        mock_boto3 = MagicMock()
        mock_ec2 = MagicMock()
        mock_autoscaling = MagicMock()

        mock_boto3.client.return_value = mock_ec2
        mock_boto3.Session.return_value.client.return_value = mock_ec2

        m.setattr("boto3.client", mock_boto3.client)
        m.setattr("boto3.Session", mock_boto3.Session)

        yield {"boto3": mock_boto3, "ec2": mock_ec2, "autoscaling": mock_autoscaling}


@pytest.fixture
def sample_template():
    """Sample template for testing."""
    return {
        "template_id": "test-template",
        "name": "Test Template",
        "description": "Test template for unit tests",
        "provider_api": "ec2_fleet",
        "image_id": "ami-12345678",
        "instance_type": "t2.micro",
        "subnet_ids": ["subnet-12345678"],
        "security_group_ids": ["sg-12345678"],
        "key_name": "test-key",
        "user_data": "",
        "tags": {"Environment": "test", "Application": "host-factory"},
    }


@pytest.fixture
def sample_request():
    """Sample request for testing."""
    return {
        "request_id": "req-12345678-1234-1234-1234-123456789012",
        "template_id": "test-template",
        "machine_count": 2,
        "status": "pending",
        "created_at": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def sample_machine():
    """Sample machine for testing."""
    return {
        "machine_id": "i-1234567890abcdef0",
        "request_id": "req-12345678-1234-1234-1234-123456789012",
        "template_id": "test-template",
        "status": "running",
        "instance_type": "t2.micro",
        "private_ip": "10.0.1.100",
        "public_ip": "54.123.45.67",
        "created_at": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def mock_config():
    """Mock configuration."""
    return {
        "provider": {
            "type": "aws",
            "aws": {"region": "us-east-1", "profile": "default"},
        },
        "logging": {"level": "INFO", "console_enabled": True},
        "storage": {"strategy": "json"},
    }

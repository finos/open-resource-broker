"""Mock environment variables for testing."""

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture
def mock_hf_environment(tmp_path):
    """
    Mock HF_PROVIDER environment variables for testing.

    Creates temporary directories and sets environment variables
    to point to test fixtures instead of the real plugin config files.

    Args:
        tmp_path: pytest temporary directory fixture

    Yields:
        Path to test config directory
    """
    # Create test directory structure
    test_config_dir = tmp_path / "config"
    test_logs_dir = tmp_path / "logs"
    test_work_dir = tmp_path / "workdir"

    test_config_dir.mkdir()
    test_logs_dir.mkdir()
    test_work_dir.mkdir()

    # Copy test fixtures to temporary directory
    fixtures_dir = Path(__file__).parent.parent / "config"

    # Copy test configuration files
    for fixture_file in fixtures_dir.glob("test_*.json"):
        # Remove 'test_' prefix for actual file names
        target_name = fixture_file.name.replace("test_", "")
        target_path = test_config_dir / target_name
        target_path.write_text(fixture_file.read_text())

    # Set environment variables
    env_vars = {
        "HF_PROVIDER_NAME": "test",
        "HF_PROVIDER_CONFDIR": str(test_config_dir),
        "HF_PROVIDER_LOGDIR": str(test_logs_dir),
        "HF_PROVIDER_WORKDIR": str(test_work_dir),
    }

    with patch.dict(os.environ, env_vars):
        yield test_config_dir


@pytest.fixture
def mock_hf_environment_with_fixtures():
    """
    Mock HF_PROVIDER environment variables pointing to test fixtures.

    Uses the actual test fixture files without copying them.
    Useful for tests that need to verify configuration loading logic.

    Yields:
        Path to test fixtures config directory
    """
    fixtures_dir = Path(__file__).parent.parent / "config"
    fixtures_logs_dir = Path(__file__).parent.parent / "logs"
    fixtures_work_dir = Path(__file__).parent.parent / "workdir"

    # Create directories if they don't exist
    fixtures_logs_dir.mkdir(exist_ok=True)
    fixtures_work_dir.mkdir(exist_ok=True)

    env_vars = {
        "HF_PROVIDER_NAME": "test-fixtures",
        "HF_PROVIDER_CONFDIR": str(fixtures_dir),
        "HF_PROVIDER_LOGDIR": str(fixtures_logs_dir),
        "HF_PROVIDER_WORKDIR": str(fixtures_work_dir),
    }

    with patch.dict(os.environ, env_vars):
        yield fixtures_dir


@pytest.fixture
def mock_aws_credentials():
    """
    Mock AWS credentials for testing.

    Sets fake AWS credentials to prevent real AWS calls during testing.
    """
    aws_env_vars = {
        "AWS_ACCESS_KEY_ID": "test-access-key",
        "AWS_SECRET_ACCESS_KEY": "test-secret-key",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
    }

    with patch.dict(os.environ, aws_env_vars):
        yield


@pytest.fixture
def complete_test_environment(mock_hf_environment, mock_aws_credentials):
    """
    Complete test environment with both HF and AWS mocking.

    Combines HF_PROVIDER environment variables and AWS credentials
    for comprehensive testing setup.

    Yields:
        Path to test config directory
    """
    yield mock_hf_environment


def create_test_config_dict() -> dict[str, Any]:
    """
    Create a test configuration dictionary for mocking.

    Returns:
        Dictionary with test configuration values
    """
    return {
        "version": "2.0.0",
        "provider": {
            "active_provider": "test-aws",
            "type": "aws",
            "config": {"region": "us-east-1", "profile": "default"},
        },
        "logging": {"level": "DEBUG", "console_enabled": True},
        "storage": {
            "strategy": "json",
            "json_strategy": {"storage_type": "single_file", "base_path": "test_data"},
        },
    }


def create_test_templates_dict() -> dict[str, Any]:
    """
    Create a test templates dictionary for mocking.

    Returns:
        Dictionary with test template definitions
    """
    return {
        "templates": [
            {
                "templateId": "test-template",
                "providerApi": "EC2Fleet",
                "fleetType": "instant",
                "maxNumber": 1,
                "imageId": "ami-12345678",
                "vmType": "t2.micro",
                "attributes": {
                    "type": ["String", "X86_64"],
                    "ncores": ["Numeric", "1"],
                    "nram": ["Numeric", "1024"],
                },
            }
        ]
    }

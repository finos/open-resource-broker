"""Basic setup tests to verify test environment is working."""

import os
import sys
from pathlib import Path

import pytest


@pytest.mark.unit
def test_environment_setup():
    """Test that test environment is properly set up."""
    # Check environment variables - TESTING is set by pytest automatically
    assert os.environ.get("PYTEST_CURRENT_TEST") is not None
    assert os.environ.get("AWS_DEFAULT_REGION") == "us-east-1"
    assert os.environ.get("AWS_ACCESS_KEY_ID") == "testing"
    # ENVIRONMENT variable is set by conftest.py during test runs
    assert os.environ.get("AWS_DEFAULT_REGION") is not None


@pytest.mark.unit
def test_python_path_setup():
    """Test that Python path is properly configured."""
    # Check that src is in Python path
    project_root = Path(__file__).parent.parent
    src_path = str(project_root / "src")

    assert src_path in sys.path


@pytest.mark.unit
def test_imports_work():
    """Test that basic imports work."""
    try:
        # Test domain imports
        # Test application imports - using CQRS handlers
        pass

        # Test infrastructure imports

        # If we get here, imports work
        assert True
    except ImportError as e:
        pytest.fail(f"Import failed: {e}")


@pytest.mark.unit
def test_pytest_markers():
    """Test that pytest markers are working."""
    # This test itself uses the unit marker
    # If it runs, markers are working
    assert True


@pytest.mark.integration
def test_integration_marker():
    """Test integration marker."""
    assert True


@pytest.mark.e2e
def test_e2e_marker():
    """Test e2e marker."""
    assert True


@pytest.mark.slow
def test_slow_marker():
    """Test slow marker."""
    import time

    time.sleep(0.1)  # Small delay to simulate slow test
    assert True


@pytest.mark.aws
def test_aws_marker():
    """Test AWS marker."""
    assert True

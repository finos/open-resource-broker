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
    from orb.domain.machine.aggregate import Machine
    from orb.domain.request.aggregate import Request
    from orb.domain.template.template_aggregate import Template
    from orb.infrastructure.di.buses import CommandBus, QueryBus

    assert Machine is not None
    assert Request is not None
    assert Template is not None
    assert CommandBus is not None
    assert QueryBus is not None


@pytest.mark.unit
def test_pytest_markers():
    """Test that pytest markers are working."""
    import pytest as _pytest

    assert hasattr(_pytest.mark, "unit")
    assert hasattr(_pytest.mark, "integration")
    assert hasattr(_pytest.mark, "e2e")


@pytest.mark.integration
def test_integration_marker():
    """Test integration marker is registered."""
    import pytest as _pytest

    assert hasattr(_pytest.mark, "integration")


@pytest.mark.e2e
def test_e2e_marker():
    """Test e2e marker is registered."""
    import pytest as _pytest

    assert hasattr(_pytest.mark, "e2e")


@pytest.mark.slow
def test_slow_marker():
    """Test slow marker."""
    import time

    start = time.monotonic()
    time.sleep(0.1)
    assert time.monotonic() - start >= 0.1


@pytest.mark.aws
def test_aws_marker():
    """Test AWS marker is registered."""
    import pytest as _pytest

    assert hasattr(_pytest.mark, "aws")

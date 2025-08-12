"""Unit tests for AMI resolution functionality.

NOTE: This test file is disabled because the AMI resolution modules
have been moved to backup/ as they are no longer part of the active
architecture. The functionality has been replaced by the new template
format service integration.

If AMI resolution is needed in the future, these tests can be re-enabled
and updated to work with the new architecture.
"""

import pytest

# Skip all tests in this file since AMI resolution is disabled
pytestmark = pytest.mark.skip(
    "AMI resolution functionality is disabled - moved to backup/"
)


@pytest.mark.unit
class TestRuntimeAMICache:
    """Placeholder test class - all tests skipped."""

    def test_placeholder(self):
        """Placeholder test."""


@pytest.mark.unit
class TestCachingAMIResolver:
    """Placeholder test class - all tests skipped."""

    def test_placeholder(self):
        """Placeholder test."""


@pytest.mark.unit
class TestResolvingTemplateConfigurationManager:
    """Placeholder test class - all tests skipped."""

    def test_placeholder(self):
        """Placeholder test."""

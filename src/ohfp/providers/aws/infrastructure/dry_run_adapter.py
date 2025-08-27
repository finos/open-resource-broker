"""AWS-specific dry-run adapter using moto for boto3 mocking.

This module provides AWS-specific dry-run functionality by integrating with moto
to mock boto3 calls when dry-run mode is active. It follows the provider-specific
adapter pattern where each provider handles its own mocking strategy.

Architecture:
- Checks global dry-run context from infrastructure layer
- Uses moto's mock_aws decorator for comprehensive AWS service mocking
- Provides realistic AWS responses for testing and demonstration
- Thread-safe and compatible with existing AWS client patterns
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager

from infrastructure.mocking.dry_run_context import is_dry_run_active

# Import moto for AWS mocking
try:
    from moto import mock_aws

    MOTO_AVAILABLE = True
except ImportError:
    MOTO_AVAILABLE = False
    mock_aws = None

logger = logging.getLogger(__name__)


@contextmanager
def aws_dry_run_context() -> Generator[None, None, None]:
    """
    AWS-specific dry-run context using moto for boto3 mocking.

    When the global dry-run context is active, this context manager
    automatically applies moto mocking to all boto3 calls within its scope.
    This provides realistic AWS responses without creating real resources.

    Yields:
        None

    Example:
        ```python
        # In AWS manager
        def create_instances(self, spec: InstanceSpec) -> List[Instance]:
            with aws_dry_run_context():
                ec2_client = self._aws_client.get_client('ec2')
                # This call is mocked if dry-run is active
                response = ec2_client.run_instances(...)
                return self._process_response(response)
        ```

    Note:
        - Only activates mocking if global dry-run context is active
        - Uses moto's comprehensive AWS service mocking
        - Provides realistic response structures
        - Maintains state across multiple calls within context
    """
    if not MOTO_AVAILABLE:
        logger.warning("Moto not available - dry-run mode will use real AWS calls")
        yield
        return

    if is_dry_run_active():
        logger.debug("DRY-RUN: AWS dry-run mode: Using moto for boto3 mocking")
        with mock_aws():
            yield
    else:
        logger.debug("AWS production mode: Using real boto3 calls")
        yield


def is_aws_dry_run_active() -> bool:
    """
    Check if AWS dry-run mode is currently active.

    This is a convenience function that combines the global dry-run check
    with AWS-specific availability checks.

    Returns:
        bool: True if AWS dry-run should be used, False otherwise
    """
    return is_dry_run_active() and MOTO_AVAILABLE


def get_aws_dry_run_status() -> dict:
    """
    Get AWS-specific dry-run status information.

    Returns:
        dict: Status information including moto availability and active state
    """
    return {
        "dry_run_active": is_dry_run_active(),
        "moto_available": MOTO_AVAILABLE,
        "aws_dry_run_active": is_aws_dry_run_active(),
        "moto_version": _get_moto_version() if MOTO_AVAILABLE else None,
    }


def _get_moto_version() -> str:
    """Get moto version for debugging purposes."""
    try:
        import moto

        return getattr(moto, "__version__", "unknown")
    except (ImportError, AttributeError):
        return "unknown"


# Context manager alias for backward compatibility
aws_mock_context = aws_dry_run_context

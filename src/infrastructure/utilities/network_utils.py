"""Network utilities with timeout handling.

This module provides utilities for network operations with proper timeout
configuration and error handling.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default timeout values (in seconds)
DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_READ_TIMEOUT = 30
DEFAULT_TOTAL_TIMEOUT = 60


class TimeoutConfig:
    """Configuration for network timeouts."""

    def __init__(
        self,
        connect: float = DEFAULT_CONNECT_TIMEOUT,
        read: float = DEFAULT_READ_TIMEOUT,
        total: Optional[float] = None
    ):
        """Initialize timeout configuration.

        Args:
            connect: Connection timeout in seconds
            read: Read timeout in seconds
            total: Total timeout in seconds (optional)
        """
        self.connect = connect
        self.read = read
        self.total = total or (connect + read)

    def as_tuple(self) -> tuple[float, float]:
        """Return timeout as (connect, read) tuple for requests library."""
        return (self.connect, self.read)

    def as_dict(self) -> dict[str, float]:
        """Return timeout as dictionary."""
        return {
            "connect": self.connect,
            "read": self.read,
            "total": self.total
        }


# Predefined timeout configurations
QUICK_TIMEOUT = TimeoutConfig(connect=5, read=10)
STANDARD_TIMEOUT = TimeoutConfig(connect=10, read=30)
LONG_TIMEOUT = TimeoutConfig(connect=15, read=60)


def get_boto3_config(
    timeout: Optional[TimeoutConfig] = None,
    max_retries: int = 3,
    **kwargs: Any
) -> Any:
    """Get boto3 Config object with timeout settings.

    Args:
        timeout: Timeout configuration
        max_retries: Maximum number of retries
        **kwargs: Additional config parameters

    Returns:
        boto3.Config object
    """
    try:
        from botocore.config import Config
    except ImportError:
        logger.warning("botocore not available, returning None config")
        return None

    timeout_config = timeout or STANDARD_TIMEOUT

    config_params = {
        "connect_timeout": timeout_config.connect,
        "read_timeout": timeout_config.read,
        "retries": {
            "max_attempts": max_retries,
            "mode": "adaptive"
        },
        **kwargs
    }

    return Config(**config_params)


def get_requests_timeout(timeout: Optional[TimeoutConfig] = None) -> tuple[float, float]:
    """Get timeout tuple for requests library.

    Args:
        timeout: Timeout configuration

    Returns:
        (connect_timeout, read_timeout) tuple
    """
    timeout_config = timeout or STANDARD_TIMEOUT
    return timeout_config.as_tuple()

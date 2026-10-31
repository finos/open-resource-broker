"""Boto3 configuration utilities for AWS provider."""

from typing import Any, Optional

from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.utilities.network_utils import STANDARD_TIMEOUT, TimeoutConfig

logger = get_logger(__name__)

# Retries botocore performs on EC2, Auto Scaling, SSM, IAM and the other clients for
# calls the AWS call executor does not retry. This was botocore's single retry (two
# HTTP requests) before the executor existed, and stays that way.
BOTOCORE_DEFAULT_RETRIES = 1

# Retries botocore itself performs for DynamoDB calls. The DynamoDB storage
# path does not go through the AWS call executor, so botocore owns throttling
# and 5xx handling there.
DYNAMODB_BOTOCORE_MAX_ATTEMPTS = 3


def get_boto3_config(
    timeout: Optional[TimeoutConfig] = None,
    max_retries: int = 3,
    retry_mode: str = "adaptive",
    single_attempt: bool = False,
    **kwargs: Any,
) -> Any:
    """Get boto3 Config object with timeout settings.

    Args:
        timeout: Timeout configuration
        max_retries: Maximum number of retries botocore performs after the first call
        retry_mode: botocore retry mode (``adaptive``, ``standard`` or ``legacy``)
        single_attempt: Disable botocore retries (one HTTP request per call); ``max_retries``
            is ignored
        **kwargs: Additional config parameters

    Returns:
        boto3.Config object
    """
    try:
        from botocore.config import Config
    except ImportError:
        logger.warning("botocore not available, returning None config", exc_info=True)
        return None

    timeout_config = timeout or STANDARD_TIMEOUT

    retries: dict[str, Any] = (
        {"total_max_attempts": 1, "mode": retry_mode}
        if single_attempt
        else {"max_attempts": max_retries, "mode": retry_mode}
    )
    config_params = {
        "connect_timeout": timeout_config.connect,
        "read_timeout": timeout_config.read,
        "retries": retries,
        **kwargs,
    }

    return Config(**config_params)

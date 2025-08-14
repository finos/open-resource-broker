"""AWS-specific retry strategy implementation."""

import secrets

from src.domain.base.dependency_injection import injectable
from src.domain.base.ports import LoggingPort
from src.infrastructure.resilience.strategy.base import RetryStrategy
from src.providers.aws.resilience.aws_retry_config import DEFAULT_AWS_RETRY_CONFIG
from src.providers.aws.resilience.aws_retry_errors import (
    get_aws_error_info,
    is_retryable_aws_error,
)

# Module-level logger replaced with injected logger


@injectable
class AWSRetryStrategy(RetryStrategy):
    """AWS-specific retry strategy with service-aware error handling."""

    def __init__(self, logger: LoggingPort, service: str = "ec2", **kwargs):
        """
        Initialize AWS retry strategy.

        Args:
            service: AWS service name (ec2, dynamodb, s3, etc.)
            **kwargs: Additional configuration parameters
        """
        self.service = service

        # Get AWS service-specific configuration
        service_config = DEFAULT_AWS_RETRY_CONFIG.get_service_config(service)

        # Override with any provided kwargs
        config = {**service_config, **kwargs}

        # Store configuration attributes
        self.max_attempts = config.get("max_attempts", 3)
        self.base_delay = config.get("base_delay", 1.0)
        self.max_delay = config.get("max_delay", 60.0)
        self.jitter = config.get("jitter", True)

        self._logger.debug(
            f"Initialized AWS retry strategy for {service}",
            extra={
                "service": service,
                "max_attempts": self.max_attempts,
                "base_delay": self.base_delay,
                "max_delay": self.max_delay,
            },
        )

    def should_retry(self, attempt: int, exception: Exception) -> bool:
        """
        Determine if an AWS operation should be retried.

        Args:
            attempt: Current attempt number (0-based)
            exception: Exception that occurred

        Returns:
            True if should retry, False otherwise
        """
        # Check attempt limit first
        if attempt >= self.max_attempts:
            return False

        # Check if it's a retryable AWS error
        if is_retryable_aws_error(exception, self.service):
            error_info = get_aws_error_info(exception)
            self._logger.info(
                f"AWS {self.service} operation failed with retryable error: {error_info['code']}",
                extra={
                    "service": self.service,
                    "attempt": attempt + 1,
                    "max_attempts": self.max_attempts,
                    "error_code": error_info["code"],
                    "error_message": error_info["message"],
                },
            )
            return True

        # Not a retryable error
        error_info = get_aws_error_info(exception)
        self._logger.debug(
            f"AWS {self.service} operation failed with non-retryable error: {error_info['code']}",
            extra={
                "service": self.service,
                "error_code": error_info["code"],
                "error_message": error_info["message"],
            },
        )
        return False

    def get_delay(self, attempt: int) -> float:
        """
        Calculate delay before next attempt using exponential backoff.

        Args:
            attempt: Current attempt number (0-based)

        Returns:
            Delay in seconds before next retry
        """
        # Exponential backoff: base_delay * (2 ^ attempt)
        delay = self.base_delay * (2**attempt)

        # Cap at max_delay
        delay = min(delay, self.max_delay)

        # Add jitter if enabled
        if self.jitter:
            jitter_range = delay * 0.1  # 10% jitter
            # Use secrets for cryptographically secure randomness
            random_float = secrets.SystemRandom().random() * 2 - 1  # Range from -1 to 1
            delay += jitter_range * random_float

        return max(0.0, delay)

    def on_retry(self, attempt: int, exception: Exception) -> None:
        """
        Handle retry event for AWS operations.

        Args:
            attempt: Current attempt number (0-based)
            exception: Exception that occurred
        """
        error_info = get_aws_error_info(exception)
        delay = self.calculate_delay(attempt)

        self._logger.warning(
            f"Retrying AWS {self.service} operation (attempt {attempt + 1}/{self.max_attempts}) "
            f"after {delay:.2f}s delay due to {error_info['code']}",
            extra={
                "service": self.service,
                "attempt": attempt + 1,
                "max_attempts": self.max_attempts,
                "delay": delay,
                "error_code": error_info["code"],
                "error_message": error_info["message"],
            },
        )

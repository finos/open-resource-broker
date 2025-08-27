"""AWS-specific retry configuration."""

from typing import Any

from pydantic import BaseModel, Field


class AWSRetryConfig(BaseModel):
    """AWS-specific retry configuration."""

    # Service-specific retry settings
    service_configs: dict[str, dict[str, Any]] = Field(
        default_factory=lambda: {
            "ec2": {
                "max_attempts": 3,
                "base_delay": 1.0,
                "max_delay": 30.0,
                "jitter": True,
            },
            "dynamodb": {
                "max_attempts": 5,
                "base_delay": 0.5,
                "max_delay": 20.0,
                "jitter": True,
            },
            "s3": {
                "max_attempts": 4,
                "base_delay": 0.5,
                "max_delay": 15.0,
                "jitter": True,
            },
            "ssm": {
                "max_attempts": 3,
                "base_delay": 1.0,
                "max_delay": 30.0,
                "jitter": True,
            },
            "iam": {
                "max_attempts": 3,
                "base_delay": 2.0,
                "max_delay": 60.0,
                "jitter": True,
            },
        },
        description="AWS service-specific retry configurations",
    )

    def get_service_config(self, service: str) -> dict[str, Any]:
        """
        Get retry configuration for a specific AWS service.

        Args:
            service: AWS service name

        Returns:
            Service-specific configuration or default values
        """
        return self.service_configs.get(
            service,
            {"max_attempts": 3, "base_delay": 1.0, "max_delay": 60.0, "jitter": True},
        )


# Default AWS retry configuration instance
DEFAULT_AWS_RETRY_CONFIG = AWSRetryConfig()

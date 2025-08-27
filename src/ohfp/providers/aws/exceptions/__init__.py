"""AWS provider exceptions."""

from providers.aws.exceptions.aws_exceptions import (
    AuthorizationError,
    AWSConfigurationError,
    AWSEntityNotFoundError,
    AWSError,
    AWSInfrastructureError,
    AWSValidationError,
    EC2InstanceNotFoundError,
    LaunchError,
    NetworkError,
    QuotaExceededError,
    RateLimitError,
    ResourceCleanupError,
    ResourceInUseError,
    ResourceStateError,
    TaggingError,
    TerminationError,
)

__all__: list[str] = [
    "AWSConfigurationError",
    "AWSEntityNotFoundError",
    "AWSError",
    "AWSInfrastructureError",
    "AWSValidationError",
    "AuthorizationError",
    "EC2InstanceNotFoundError",
    "LaunchError",
    "NetworkError",
    "QuotaExceededError",
    "RateLimitError",
    "ResourceCleanupError",
    "ResourceInUseError",
    "ResourceStateError",
    "TaggingError",
    "TerminationError",
]

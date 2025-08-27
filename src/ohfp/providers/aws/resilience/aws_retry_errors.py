"""AWS-specific retry error definitions."""

# AWS service-specific retryable errors
AWS_RETRYABLE_ERRORS: dict[str, list[str]] = {
    "ec2": [
        "RequestLimitExceeded",
        "InsufficientInstanceCapacity",
        "InternalError",
        "ServiceUnavailable",
        "Unavailable",
    ],
    "dynamodb": [
        "ProvisionedThroughputExceededException",
        "ThrottlingException",
        "RequestLimitExceeded",
        "InternalServerError",
        "ServiceUnavailable",
    ],
    "s3": [
        "SlowDown",
        "ServiceUnavailable",
        "InternalError",
        "RequestTimeout",
        "BandwidthLimitExceeded",
    ],
    "ssm": [
        "ThrottlingException",
        "RequestLimitExceeded",
        "InternalServerError",
        "ServiceUnavailable",
    ],
    "iam": ["Throttling", "ServiceFailure", "ServiceUnavailable"],
}

# Common AWS throttling errors that should always be retried
COMMON_AWS_THROTTLING_ERRORS = [
    "Throttling",
    "ThrottlingException",
    "RequestLimitExceeded",
    "TooManyRequestsException",
    "ProvisionedThroughputExceededException",
    "SlowDown",
]


def is_retryable_aws_error(exception: Exception, service: str = "ec2") -> bool:
    """
    Check if an AWS exception is retryable.

    Args:
        exception: Exception to check
        service: AWS service name

    Returns:
        True if exception is retryable, False otherwise
    """
    # Check if it's a boto3 ClientError with response
    if not hasattr(exception, "response") or not isinstance(exception.response, dict):
        return False

    error_code = exception.response.get("Error", {}).get("Code", "")

    # Check service-specific retryable errors
    service_errors = AWS_RETRYABLE_ERRORS.get(service, [])
    if error_code in service_errors:
        return True

    # Check common throttling errors
    if error_code in COMMON_AWS_THROTTLING_ERRORS:
        return True

    return False


def get_aws_error_info(exception: Exception) -> dict[str, str]:
    """
    Extract AWS error information from exception.

    Args:
        exception: AWS exception

    Returns:
        Dictionary with error code and message
    """
    if hasattr(exception, "response") and isinstance(exception.response, dict):
        error_info = exception.response.get("Error", {})
        return {
            "code": error_info.get("Code", "Unknown"),
            "message": error_info.get("Message", str(exception)),
        }

    return {"code": "Unknown", "message": str(exception)}

"""AWS-specific retry error definitions."""

# Error codes that are always transient, whichever AWS service returned them.
# This is the single source for the retry classifier.  Quota and
# authorisation codes are deliberately absent, and so is
# ``InsufficientInstanceCapacity``: capacity shortfalls are handled by the
# capacity fallback, not by repeating the identical call.
AWS_TRANSIENT_ERROR_CODES: frozenset[str] = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "ThrottledException",
        "EC2ThrottledException",
        "RequestThrottled",
        "RequestThrottledException",
        "Throttled",
        "RequestLimitExceeded",
        "TooManyRequestsException",
        "ProvisionedThroughputExceededException",
        "TransactionInProgressException",
        "PriorRequestNotComplete",
        "SlowDown",
        "BandwidthLimitExceeded",
        "RequestTimeout",
        "RequestTimeoutException",
        "InternalError",
        "InternalFailure",
        "ServiceUnavailable",
        "Unavailable",
        # Auto Scaling group teardown races (a scaling activity or a
        # dependent resource releases within seconds).
        "ScalingActivityInProgress",
        "ResourceInUse",
    }
)

# Capacity and quota answers that EC2 returns with a retryable HTTP status (500).
# They are never transient for the same request: repeating the identical call
# cannot succeed and only feeds the circuit breaker. Checked before the HTTP
# status fallback.
AWS_NEVER_RETRY_ERROR_CODES: frozenset[str] = frozenset(
    {
        "InsufficientInstanceCapacity",
        "InsufficientHostCapacity",
        "InsufficientReservedInstanceCapacity",
        "InsufficientCapacity",
        "InsufficientAddressCapacity",
        "InsufficientFreeAddressesInSubnet",
        "InsufficientVolumeCapacity",
        "MaxSpotInstanceCountExceeded",
        "InstanceLimitExceeded",
        "VcpuLimitExceeded",
        "LimitExceeded",
        "ServiceQuotaExceededException",
        "ResourceCountExceeded",
        "SpotMaxPriceTooLow",
        "UnfulfillableCapacity",
        "Unsupported",
        "UnsupportedOperation",
        "OptInRequired",
    }
)

# Codes botocore only self-corrects on its own retry (it records the clock
# offset from the response on the first failure).  With botocore retries off
# the application retries them and the next attempt is signed correctly.
AWS_CLOCK_SKEW_ERROR_CODES: frozenset[str] = frozenset(
    {"RequestExpired", "RequestInTheFuture", "RequestTimeTooSkewed"}
)

# Signature errors are transient only when the local clock is off.
AWS_SKEW_DEPENDENT_ERROR_CODES: frozenset[str] = frozenset(
    {"InvalidSignatureException", "SignatureDoesNotMatch"}
)

# HTTP statuses retried regardless of the error code.
AWS_RETRYABLE_HTTP_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Clock offset (seconds) beyond which a signature error is attributed to skew.
AWS_CLOCK_SKEW_THRESHOLD_SECONDS = 300

# Per-service view kept for callers that ask about one service.
AWS_RETRYABLE_ERRORS: dict[str, list[str]] = {
    "ec2": [
        "RequestLimitExceeded",
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
    if not hasattr(exception, "response") or not isinstance(exception.response, dict):  # type: ignore[union-attr]
        return False

    error_code = exception.response.get("Error", {}).get("Code", "")  # type: ignore[union-attr]

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
    if hasattr(exception, "response") and isinstance(exception.response, dict):  # type: ignore[union-attr]
        error_info = exception.response.get("Error", {})  # type: ignore[union-attr]
        return {
            "code": error_info.get("Code", "Unknown"),
            "message": error_info.get("Message", str(exception)),
        }

    return {"code": "Unknown", "message": str(exception)}

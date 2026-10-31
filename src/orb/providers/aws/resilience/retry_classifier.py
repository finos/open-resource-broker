"""AWS retry classifier.

Registered with the provider-agnostic retry classifier registry so the
resilience layer retries only transient AWS failures (throttling, 5xx,
connection problems, clock skew) and fails permanent ones (validation,
authorisation, quota, not-found) on the first attempt without sleeping or
counting against a circuit breaker.
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Iterator
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from typing import Any, Optional

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectionError as BotoConnectionError,
    ConnectTimeoutError,
    EndpointConnectionError,
    HTTPClientError,
    ProxyConnectionError,
    ReadTimeoutError,
    SSLError,
)

from orb.providers.aws.resilience.aws_retry_errors import (
    AWS_CLOCK_SKEW_ERROR_CODES,
    AWS_CLOCK_SKEW_THRESHOLD_SECONDS,
    AWS_NEVER_RETRY_ERROR_CODES,
    AWS_RETRYABLE_HTTP_STATUS_CODES,
    AWS_SKEW_DEPENDENT_ERROR_CODES,
    AWS_TRANSIENT_ERROR_CODES,
)

_TRANSIENT_BOTOCORE_ERRORS: tuple[type[BotoCoreError], ...] = (
    ConnectTimeoutError,
    ReadTimeoutError,
    EndpointConnectionError,
    ConnectionClosedError,
    ProxyConnectionError,
    BotoConnectionError,
    HTTPClientError,
    SSLError,
)

# Per-call overrides, scoped to the executing call by ``call_overrides``.
_extra_retryable: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "aws_extra_retryable_codes", default=frozenset()
)
_extra_non_retryable: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "aws_extra_non_retryable_codes", default=frozenset()
)


@contextmanager
def call_overrides(
    retryable: Optional[frozenset[str]] = None,
    non_retryable: Optional[frozenset[str]] = None,
) -> Iterator[None]:
    """Scope extra retryable / non-retryable error codes to one call."""
    tokens = [
        _extra_retryable.set(frozenset(retryable or ())),
        _extra_non_retryable.set(frozenset(non_retryable or ())),
    ]
    try:
        yield
    finally:
        _extra_non_retryable.reset(tokens[1])
        _extra_retryable.reset(tokens[0])


def error_code(exception: BaseException) -> str:
    """Return the AWS error code carried by *exception* ("" when absent)."""
    response = getattr(exception, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", "") or "")
    return ""


def _clock_is_skewed(response: dict[str, Any]) -> bool:
    """True when the response ``Date`` header differs from local time by > 300 s."""
    headers = response.get("ResponseMetadata", {}).get("HTTPHeaders", {}) or {}
    date_value = next((v for k, v in headers.items() if str(k).lower() == "date"), None)
    if not date_value:
        return False
    try:
        server_time = parsedate_to_datetime(str(date_value)).timestamp()
    except (TypeError, ValueError):
        return False
    return abs(time.time() - server_time) > AWS_CLOCK_SKEW_THRESHOLD_SECONDS


def is_retryable_client_error(exception: ClientError) -> bool:
    """Return True when a ``ClientError`` is transient."""
    code = error_code(exception)
    if code in AWS_NEVER_RETRY_ERROR_CODES:
        return False
    if code in AWS_TRANSIENT_ERROR_CODES or code in AWS_CLOCK_SKEW_ERROR_CODES:
        return True
    if code in AWS_SKEW_DEPENDENT_ERROR_CODES:
        return _clock_is_skewed(exception.response)
    status = exception.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status in AWS_RETRYABLE_HTTP_STATUS_CODES


class AWSRetryClassifier:
    """Flag permanent AWS failures; leave unknown exception types alone."""

    def is_non_retryable(self, exception: Exception) -> bool:
        if isinstance(exception, ClientError):
            code = error_code(exception)
            if code and code in _extra_non_retryable.get():
                return True
            if code and code in _extra_retryable.get():
                return False
            return not is_retryable_client_error(exception)
        if isinstance(exception, BotoCoreError):
            return not isinstance(exception, _TRANSIENT_BOTOCORE_ERRORS)
        return False

    # Identity for the registry's idempotent registration.
    def __eq__(self, other: object) -> bool:
        return isinstance(other, AWSRetryClassifier)

    def __hash__(self) -> int:
        return hash(AWSRetryClassifier)


def register_aws_retry_classifier() -> None:
    """Register the AWS classifier with the shared registry (idempotent)."""
    from orb.infrastructure.resilience.retry_classifier_registry import (
        register_retry_classifier,
    )

    register_retry_classifier(AWSRetryClassifier())

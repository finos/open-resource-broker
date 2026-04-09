"""GCP provider exceptions and translation helpers."""

from __future__ import annotations

from typing import Any, Optional

from orb.domain.base.exceptions import (
    InfrastructureError,
    QuotaExceededError as DomainQuotaExceededError,
)


class GCPError(InfrastructureError):
    """Base class for GCP-related runtime errors."""

    def __init__(
        self,
        message: str,
        details: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ) -> None:
        super().__init__(message, error_code or self.__class__.__name__, details)
        self.error_code = error_code or self.__class__.__name__

    def to_dict(self) -> dict[str, Any]:
        """Convert the error to a structured dictionary."""
        result: dict[str, Any] = super().to_dict()  # type: ignore[attr-defined]
        if self.error_code and self.error_code != self.__class__.__name__:
            result["error_code"] = self.error_code
        return result


class GCPValidationError(GCPError):
    """Raised when GCP request or target validation fails."""


class GCPEntityNotFoundError(GCPError):
    """Raised when a GCP resource cannot be found."""


class GCPQuotaExceededError(GCPError, DomainQuotaExceededError):
    """Raised when a GCP quota would be exceeded."""


class GCPAuthorizationError(GCPError):
    """Raised when credentials or permissions are insufficient."""


class GCPRateLimitError(GCPError):
    """Raised when the GCP API throttles a request."""


class GCPNetworkError(GCPError):
    """Raised for transient network or service-availability failures."""


class GCPInfrastructureError(GCPError):
    """Raised for uncategorized GCP infrastructure failures."""


class GCPConfigurationError(GCPError):
    """Raised when the GCP provider is configured incorrectly."""


try:
    from google.api_core import exceptions as google_exceptions
except ImportError:  # pragma: no cover - exercised only when optional sdk deps are absent
    google_exceptions = None


def translate_gcp_exception(
    exc: Exception,
    *,
    operation: str,
    details: Optional[dict[str, Any]] = None,
) -> GCPError:
    """Translate raw runtime failures into the provider-local exception hierarchy."""
    if isinstance(exc, GCPError):
        return exc

    translated_details = {
        "operation": operation,
        "source_error_type": exc.__class__.__name__,
        **_extract_error_details(exc),
        **(details or {}),
    }
    message = str(exc) or exc.__class__.__name__

    if isinstance(exc, ValueError):
        return GCPValidationError(message, details=translated_details)

    mapped_error = _translate_google_api_exception(exc, message, translated_details)
    if mapped_error is not None:
        return mapped_error
    if isinstance(exc, RuntimeError) and "required for gcp" in message.lower():
        return GCPConfigurationError(message, details=translated_details)
    return GCPInfrastructureError(message, details=translated_details)


def _translate_google_api_exception(
    exc: Exception,
    message: str,
    details: dict[str, Any],
) -> GCPError | None:
    if google_exceptions is None:
        return None

    if isinstance(exc, google_exceptions.NotFound):
        return GCPEntityNotFoundError(message, details=details)
    if isinstance(
        exc,
        (
            google_exceptions.Forbidden,
            google_exceptions.Unauthorized,
        ),
    ):
        return GCPAuthorizationError(message, details=details)
    if isinstance(exc, google_exceptions.ResourceExhausted):
        return GCPQuotaExceededError(message, details=details)
    if isinstance(exc, google_exceptions.TooManyRequests):
        return GCPRateLimitError(message, details=details)
    if isinstance(
        exc,
        (
            google_exceptions.DeadlineExceeded,
            google_exceptions.ServiceUnavailable,
            google_exceptions.BadGateway,
            google_exceptions.GatewayTimeout,
            google_exceptions.InternalServerError,
        ),
    ):
        return GCPNetworkError(message, details=details)
    return None


def _extract_error_details(exc: Exception) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if google_exceptions is not None and isinstance(exc, google_exceptions.GoogleAPICallError):
        if exc.code is not None:
            details["google_error_code"] = str(exc.code)
        status_code = _extract_status_code(exc)
        if status_code:
            details["http_status_code"] = status_code
        if exc.reason:
            details["reason"] = exc.reason
        if exc.domain:
            details["domain"] = exc.domain
        if exc.metadata:
            details["metadata"] = dict(exc.metadata)
        if exc.errors:
            details["errors"] = exc.errors
        if exc.details:
            details["rpc_details"] = [str(item) for item in exc.details]
    return details


def _extract_status_code(exc: google_exceptions.GoogleAPICallError) -> str | None:
    response = exc.response
    if response is not None and hasattr(response, "status_code"):
        response_status = getattr(response, "status_code")
        if response_status is not None:
            return str(response_status)
    return None

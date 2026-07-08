"""Helpers for normalizing Azure SDK / ARM error payloads."""

from __future__ import annotations

from typing import Any


def _json_response_body(response: Any) -> dict[str, Any] | None:
    """Best-effort response JSON extraction for heterogeneous SDK responses.

    Uses getattr because Azure SDK response objects do not share a typed protocol
    for optional JSON bodies across sync and async transports.
    """
    if response is None:
        return None
    json_method = getattr(response, "json", None)
    if not callable(json_method):
        return None
    try:
        body = json_method()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _normalise_error_details(details: Any) -> list[dict[str, Any]]:
    """Normalise Azure nested error details from dicts or SDK error objects.

    Uses getattr because nested Azure error items may be plain dictionaries or
    SDK model instances depending on where the exception was raised.
    """
    if not isinstance(details, list):
        return []

    normalised: list[dict[str, Any]] = []
    for item in details:
        if isinstance(item, dict):
            normalised.append(item)
            continue
        code = getattr(item, "code", None)
        message = getattr(item, "message", None)
        if code is None and message is None:
            continue
        normalised.append({
            "code": str(code) if code not in (None, "") else None,
            "message": str(message) if message not in (None, "") else None,
        })
    return normalised


def extract_azure_error_details(exc: Exception) -> dict[str, Any]:
    """Extract Azure SDK error details from common exception shapes.

    Uses getattr throughout because this normalises errors from the full
    azure-core hierarchy (HttpResponseError, ServiceRequestError, ODataV4Error,
    etc.) plus non-Azure exceptions.  No single base class exposes all of
    error / response / error_code / status_code / message uniformly.
    """
    error = getattr(exc, "error", None)
    response = getattr(exc, "response", None)
    exception_details = getattr(exc, "details", None)
    response_body = _json_response_body(response)
    response_error = response_body.get("error") if isinstance(response_body, dict) else None

    raw_error_code = (
        getattr(exc, "error_code", None)
        or getattr(error, "code", None)
        or getattr(exc, "code", None)
        or (
            exception_details.get("raw_error_code")
            if isinstance(exception_details, dict)
            else None
        )
        or (response_error.get("code") if isinstance(response_error, dict) else None)
    )
    status_code = getattr(exc, "status_code", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if status_code is None and isinstance(exception_details, dict):
        status_code = exception_details.get("status_code")

    message = (
        getattr(error, "message", None)
        or getattr(exc, "message", None)
        or (
            exception_details.get("error_message")
            if isinstance(exception_details, dict)
            else None
        )
        or (response_error.get("message") if isinstance(response_error, dict) else None)
        or str(exc)
    )
    details = _normalise_error_details(getattr(error, "details", None))
    if not details and isinstance(exception_details, dict):
        details = _normalise_error_details(exception_details.get("details"))
    if not details and isinstance(response_error, dict):
        details = _normalise_error_details(response_error.get("details"))

    return {
        "raw_error_code": str(raw_error_code) if raw_error_code not in (None, "") else None,
        "status_code": status_code,
        "message": str(message),
        "details": details,
    }


def canonical_azure_error_code(exc: Exception) -> str:
    """Return a stable Azure provisioning error code."""
    details = extract_azure_error_details(exc)
    raw_error_code = details["raw_error_code"]
    status_code = details["status_code"]
    message = details["message"].lower()

    if raw_error_code:
        return raw_error_code
    if status_code == 429:
        return "TooManyRequests"
    if status_code == 404:
        return "ResourceNotFound"
    if status_code == 403:
        return "AuthorizationFailed"
    if "quota" in message or "exceed" in message:
        return "QuotaExceeded"
    if "allocationfailed" in message or "insufficient" in message:
        return "AllocationFailed"
    if "validation" in message or "invalid" in message:
        return "InvalidRequest"
    return type(exc).__name__


_QUOTA_CODES = frozenset({"QuotaExceeded", "OperationNotAllowed", "ResourceQuotaExceeded"})
_VALIDATION_CODES = frozenset({"InvalidRequest", "InvalidParameter", "BadRequest"})


def classify_azure_error(exc: Exception) -> tuple[str, str]:
    """Classify an Azure exception as ``("quota"|"validation"|"other", error_code)``.

    Canonical Azure error codes take precedence. Message-based string matching
    is only consulted when the canonical mapping fell back to ``type(exc).__name__``
    — without that guard, tag or resource names containing "quota" or "exceed"
    can misclassify unrelated errors.
    """
    error_code = canonical_azure_error_code(exc)

    if error_code in _QUOTA_CODES:
        return ("quota", error_code)
    if error_code in _VALIDATION_CODES:
        return ("validation", error_code)

    if error_code == type(exc).__name__:
        message = extract_azure_error_details(exc)["message"].lower()
        if "quota" in message or "exceeded" in message:
            return ("quota", error_code)
        if "validation" in message or "invalid" in message:
            return ("validation", error_code)

    return ("other", error_code)

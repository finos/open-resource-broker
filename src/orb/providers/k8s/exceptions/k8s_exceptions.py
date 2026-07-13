"""Kubernetes provider exception hierarchy.

Mirrors :mod:`orb.providers.aws.exceptions.aws_exceptions` in structure:
a structured base class (``K8sError``) with typed sub-classes that each map
to a specific Kubernetes API failure category, plus a classifier that turns a
raw ``kubernetes.client.exceptions.ApiException`` into the correct typed
exception.

Usage
-----

At every Kubernetes API-call boundary in the handler layer::

    from orb.providers.k8s.exceptions.k8s_exceptions import classify_api_exception

    try:
        result = core_v1.create_namespaced_pod(...)
    except ApiException as exc:
        raise classify_api_exception(exc, operation="create_namespaced_pod") from exc

The classifier is *not* called inside ``with_retry`` because the retry
classifier needs to inspect the raw ``ApiException`` to decide whether to
retry.  The translate-to-typed step happens after the retry budget is
exhausted (i.e. at the ``except`` boundary in each handler method), so
callers and logs receive the rich typed exception rather than the raw SDK
object.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from orb.domain.base.exceptions import (
    InfrastructureError,
    QuotaExceededError as DomainQuotaExceededError,
)


class K8sError(InfrastructureError):
    """Base class for all Kubernetes provider-specific errors.

    Structured fields mirror :class:`orb.providers.aws.exceptions.aws_exceptions.AWSError`
    for consistent cross-provider error handling.

    Args:
        message: Human-readable error message.
        details: Additional error details and context.
        error_code: Domain-level error code for programmatic handling.
        http_status: HTTP status code from the Kubernetes API response (e.g. 403).
        k8s_reason: ``reason`` field from the Kubernetes API Status object
            (e.g. ``"Forbidden"``, ``"AlreadyExists"``).
        k8s_message: ``message`` field from the Kubernetes API Status object.
        request_id: Kubernetes request ID extracted from response headers or body,
            useful for debugging with apiserver audit logs.
        error_source: The Kubernetes API operation that failed
            (e.g. ``"kubernetes.pod.create_namespaced_pod"``).
    """

    def __init__(
        self,
        message: str,
        details: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
        http_status: Optional[int] = None,
        k8s_reason: Optional[str] = None,
        k8s_message: Optional[str] = None,
        request_id: Optional[str] = None,
        error_source: Optional[str] = None,
    ) -> None:
        super().__init__(message, error_code or self.__class__.__name__, details)
        self.error_code = error_code or self.__class__.__name__
        self.http_status = http_status
        self.k8s_reason = k8s_reason
        self.k8s_message = k8s_message
        self.request_id = request_id
        self.error_source = error_source

    def to_dict(self) -> dict[str, Any]:
        """Convert error to dictionary for structured logging and API responses."""
        result: dict[str, Any] = super().to_dict()  # type: ignore[attr-defined]
        if self.error_code and self.error_code != self.__class__.__name__:
            result["error_code"] = self.error_code
        if self.http_status is not None:
            result["http_status"] = self.http_status
        if self.k8s_reason:
            result["k8s_reason"] = self.k8s_reason
        if self.k8s_message:
            result["k8s_message"] = self.k8s_message
        if self.request_id:
            result["request_id"] = self.request_id
        if self.error_source:
            result["error_source"] = self.error_source
        return result


# ---------------------------------------------------------------------------
# Typed sub-classes — one per failure category
# ---------------------------------------------------------------------------


class K8sAuthError(K8sError):
    """Raised when Kubernetes API client authentication / config loading fails.

    Distinct from :class:`K8sAuthorizationError` (RBAC 403 from the apiserver):
    this class covers *client-side* failures such as missing kubeconfig or
    service account token loading errors that occur before a request is sent.
    """


class K8sAuthenticationError(K8sError):
    """Raised when the Kubernetes apiserver returns 401 Unauthorized.

    Indicates the caller's credentials were rejected — typically an expired
    ServiceAccount token or a missing ``Authorization`` header.  This is
    distinct from :class:`K8sAuthError` (client-side config loading failure)
    and :class:`K8sAuthorizationError` (403 RBAC denial after authentication
    succeeded).

    A 401 is non-retryable: the same expired token will continue to be rejected
    until it is refreshed.  The caller should refresh credentials and re-issue
    rather than burning retry budget.
    """


class K8sHealthCheckError(K8sError):
    """Raised when the Kubernetes API server health check fails."""


class K8sDiscoveryError(K8sError):
    """Raised when Kubernetes infrastructure discovery encounters a non-recoverable error.

    Distinct from :class:`K8sAuthError` (authentication / config loading) and
    :class:`K8sHealthCheckError` (server reachability).  ``K8sDiscoveryError`` is
    raised when a specific discovery call fails in a way that prevents the
    discovery flow from continuing — for example a 404 on a named namespace that
    was expected to exist, or a kubeconfig that is absent or malformed.
    """


class K8sAuthorizationError(K8sError):
    """Raised when the Kubernetes apiserver returns 403 Forbidden (RBAC denial).

    Distinct from :class:`K8sAuthError` (client-side auth failure):
    ``K8sAuthorizationError`` is a server-side rejection after the request
    was sent and the server evaluated RBAC policies.
    """


class K8sQuotaExceededError(K8sError, DomainQuotaExceededError):
    """Raised when a Kubernetes resource quota would be exceeded.

    Maps to a 403 Forbidden response where the Status body mentions quota
    (``"exceeded quota"`` in the message).  Combines with the domain
    :class:`~orb.domain.base.exceptions.QuotaExceededError` so cross-provider
    quota handlers can catch the domain type.
    """


class K8sRateLimitError(K8sError):
    """Raised when the Kubernetes apiserver returns 429 Too Many Requests."""


class K8sConflictError(K8sError):
    """Raised when the Kubernetes apiserver returns 409 Conflict.

    Typical cause: a resource with the same name already exists (duplicate
    create) or an optimistic-concurrency resourceVersion mismatch on a patch.
    """


class K8sEntityNotFoundError(K8sError):
    """Raised when the Kubernetes apiserver returns 404 Not Found."""


class K8sValidationError(K8sError):
    """Raised when the Kubernetes apiserver returns 422 Unprocessable Entity.

    Indicates the submitted resource spec failed server-side validation
    (e.g. unknown fields, invalid field values, missing required fields).
    """


# ---------------------------------------------------------------------------
# ApiException body parser
# ---------------------------------------------------------------------------


def _parse_api_exception_body(exc: Any) -> dict[str, Any]:
    """Extract structured fields from a ``kubernetes.client.exceptions.ApiException``.

    The ``ApiException.body`` field is a JSON string in the Kubernetes
    ``Status`` object format::

        {
            "kind": "Status",
            "apiVersion": "v1",
            "metadata": {},
            "status": "Failure",
            "message": "pods \"orb-abc\" already exists",
            "reason": "AlreadyExists",
            "details": {...},
            "code": 409
        }

    Returns a dict with keys ``reason``, ``message``, and ``request_id``
    (all optional strings).  Falls back to empty strings on parse errors so
    callers never have to guard against exceptions from this helper.
    """
    result: dict[str, Any] = {"reason": None, "message": None, "request_id": None}

    body = getattr(exc, "body", None)
    if not body:
        return result

    try:
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8", errors="replace")
        parsed = json.loads(body)
        result["reason"] = parsed.get("reason") or None
        result["message"] = parsed.get("message") or None
    except (json.JSONDecodeError, ValueError, TypeError):
        # Non-JSON body (e.g. plain text from an intermediary proxy).
        if isinstance(body, str):
            result["message"] = body[:512]  # truncate to avoid huge strings

    # Kubernetes-specific request ID is surfaced in the ``X-Request-ID``
    # response header.  Some SDK versions expose it; fall back to ``None``.
    headers = getattr(exc, "headers", None) or {}
    request_id = None
    if isinstance(headers, dict):
        request_id = (
            headers.get("X-Request-ID")
            or headers.get("x-request-id")
            or headers.get("X-Kubernetes-Request-ID")
        )
    result["request_id"] = request_id or None

    return result


# ---------------------------------------------------------------------------
# Classifier — ApiException → typed K8sError
# ---------------------------------------------------------------------------


def classify_api_exception(
    exc: Any,
    *,
    operation: Optional[str] = None,
) -> K8sError:
    """Classify a ``kubernetes.client.exceptions.ApiException`` into a typed K8sError.

    The mapping is:

    +---------+--------------------------------------+---------------------------+
    | Status  | Condition                            | Exception class           |
    +=========+======================================+===========================+
    | 401     | —                                    | K8sAuthenticationError    |
    +---------+--------------------------------------+---------------------------+
    | 404     | —                                    | K8sEntityNotFoundError    |
    +---------+--------------------------------------+---------------------------+
    | 409     | —                                    | K8sConflictError          |
    +---------+--------------------------------------+---------------------------+
    | 422     | —                                    | K8sValidationError        |
    +---------+--------------------------------------+---------------------------+
    | 429     | —                                    | K8sRateLimitError         |
    +---------+--------------------------------------+---------------------------+
    | 403     | "exceeded quota" in body             | K8sQuotaExceededError     |
    +---------+--------------------------------------+---------------------------+
    | 403     | otherwise                            | K8sAuthorizationError     |
    +---------+--------------------------------------+---------------------------+
    | other   | —                                    | K8sError (base)           |
    +---------+--------------------------------------+---------------------------+

    Args:
        exc: A ``kubernetes.client.exceptions.ApiException`` instance (or any
            exception with a ``.status`` integer attribute).
        operation: Optional Kubernetes API operation name for the
            ``error_source`` field (e.g. ``"create_namespaced_pod"``).

    Returns:
        A :class:`K8sError` sub-class instance whose structured fields are
        populated from the exception body.
    """
    status: int = int(getattr(exc, "status", 0) or 0)
    parsed = _parse_api_exception_body(exc)

    k8s_reason: Optional[str] = parsed["reason"]
    k8s_message: Optional[str] = parsed["message"]
    request_id: Optional[str] = parsed["request_id"]

    error_source: Optional[str] = f"kubernetes.{operation}" if operation else None

    # Build the base_message from the structured k8s_message when available so
    # callers get a clean, bounded string.  Fall back to str(exc) but cap it at
    # 512 chars: the SDK may embed the full apiserver Status / RBAC body which
    # can be several kilobytes and often contains sensitive detail.
    _raw_exc_str = str(exc)
    if not _raw_exc_str or _raw_exc_str in ("None", ""):
        base_message = k8s_message or f"Kubernetes API error (HTTP {status})"
    else:
        # Prefer the structured k8s_message (already parsed, bounded) when present.
        base_message = k8s_message or _raw_exc_str[:512]

    common_kwargs: dict[str, Any] = {
        "http_status": status or None,
        "k8s_reason": k8s_reason,
        "k8s_message": k8s_message,
        "request_id": request_id,
        "error_source": error_source,
    }

    if status == 404:
        return K8sEntityNotFoundError(base_message, **common_kwargs)

    if status == 409:
        return K8sConflictError(base_message, **common_kwargs)

    if status == 422:
        return K8sValidationError(base_message, **common_kwargs)

    if status == 429:
        return K8sRateLimitError(base_message, **common_kwargs)

    if status == 401:
        return K8sAuthenticationError(base_message, **common_kwargs)

    if status == 403:
        body_str = str(getattr(exc, "body", "") or "")
        if "exceeded quota" in body_str.lower():
            return K8sQuotaExceededError(base_message, **common_kwargs)
        return K8sAuthorizationError(base_message, **common_kwargs)

    return K8sError(base_message, **common_kwargs)

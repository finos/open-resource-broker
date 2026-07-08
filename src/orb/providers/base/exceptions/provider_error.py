"""Shared provider exception hierarchy for cross-provider consistency.

Every provider (AWS, k8s, Azure, GCP, OCI, …) should raise exceptions from
this hierarchy rather than inventing ad-hoc exception types.  The common base
carries ``provider_type`` so error-handling code at the application layer can
branch by provider without inspecting the concrete subclass.

Hierarchy
---------
``ProviderError``                 -- base; all provider errors (is_retryable=False)
  ``ProviderConfigError``         -- bad or missing configuration at setup time
  ``ProviderAuthError``           -- authentication / authorisation failures
  ``ProviderQuotaError``          -- quota, rate-limit, or capacity ceiling hit (is_retryable=True)
  ``ProviderTransientError``      -- retryable transient failure (is_retryable=True)
  ``ProviderPermanentError``      -- non-retryable permanent failure

Retry axis
----------
Every exception in this hierarchy carries an ``is_retryable`` boolean that
indicates whether a retry is appropriate.  Retry logic can use a single
attribute check rather than maintaining a hard-coded mapping of class names:

::

    if isinstance(exc, ProviderError) and exc.is_retryable:
        schedule_retry(exc)

Leaf classes override ``_default_is_retryable`` to set their default;
callers may also pass ``is_retryable`` explicitly to override the class
default for unusual cases.

Serialisation safety
--------------------
``to_dict()`` includes ``underlying_exception`` (via ``repr()``) which may
contain connection strings, ARNs, or other secrets.  Use ``safe_to_dict()``
for any output that leaves the process boundary (HTTP responses, audit logs
sent to external systems).  ``to_dict()`` is retained for internal structured
logging where the full context is valuable.

Provider packages sub-class exactly one of these five leaf types (or
``ProviderError`` directly when none fits) and add their own extra fields.

Example
-------
::

    from orb.providers.base.exceptions import ProviderAuthError

    raise ProviderAuthError(
        "IAM role does not have ec2:RunInstances",
        provider_type="aws",
        provider_name="prod-us-east-1",
        underlying_exception=original_boto_error,
    )
"""

from __future__ import annotations

from typing import Any, Optional


class ProviderError(Exception):
    """Base exception for all provider errors.

    All provider-specific exception classes must inherit from this class
    (directly or through one of its subclasses) so that callers can catch
    any provider error with a single ``except ProviderError`` clause.

    Attributes
    ----------
    provider_type:
        Short identifier for the provider that raised the error
        (e.g. ``"aws"``, ``"k8s"``, ``"azure"``).  Always present.
    provider_name:
        Optional human-readable name for the specific provider instance
        (e.g. ``"prod-us-east-1"``).  ``None`` when not applicable.
    underlying_exception:
        The original exception that caused this error, when applicable.
        Stored so callers can inspect or re-raise the root cause without
        losing it to the wrapper.
    is_retryable:
        Whether retrying the operation is appropriate for this error.
        Leaf classes set a class-level default via ``_default_is_retryable``;
        callers may override by passing ``is_retryable`` explicitly.
        Retry logic should check this attribute rather than using isinstance
        checks on leaf classes::

            if isinstance(exc, ProviderError) and exc.is_retryable:
                schedule_retry(exc)
    """

    #: Class-level default for ``is_retryable``.  Leaf classes override this.
    _default_is_retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        provider_type: str,
        provider_name: Optional[str] = None,
        underlying_exception: Optional[BaseException] = None,
        details: Optional[dict[str, Any]] = None,
        is_retryable: Optional[bool] = None,
    ) -> None:
        """Initialise a provider error.

        Parameters
        ----------
        message:
            Human-readable description of what went wrong.
        provider_type:
            Short provider identifier.  Required so application-layer error
            handlers can route the error without ``isinstance`` checks on
            provider-specific subclasses.
        provider_name:
            Optional name of the specific provider instance.
        underlying_exception:
            Original exception that triggered this one, if any.
        details:
            Arbitrary key/value context for structured logging or API
            responses.  Merged into ``to_dict()`` output.
        is_retryable:
            Override the class default for ``is_retryable``.  Pass ``True``
            or ``False`` to force a specific value; omit (``None``) to use
            the leaf class default.
        """
        super().__init__(message)
        self.message = message
        self.provider_type = provider_type
        self.provider_name = provider_name
        self.underlying_exception = underlying_exception
        self.details: dict[str, Any] = details or {}
        self.is_retryable: bool = (
            is_retryable if is_retryable is not None else self._default_is_retryable
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a serialisable representation of this error for internal logging.

        WARNING: This method includes ``underlying_exception`` via ``repr()``,
        which may contain sensitive information such as connection strings,
        ARNs, or credentials embedded in exception messages.  Do NOT use this
        method for HTTP responses or any output that crosses a trust boundary.
        Use ``safe_to_dict()`` instead for external-facing serialisation.

        Sub-classes should call ``super().to_dict()`` and update the returned
        dict with their own fields:

        ::

            def to_dict(self) -> dict[str, Any]:
                result = super().to_dict()
                result["quota_name"] = self.quota_name
                return result
        """
        result: dict[str, Any] = {
            "error_type": type(self).__name__,
            "message": self.message,
            "provider_type": self.provider_type,
            "is_retryable": self.is_retryable,
        }
        if self.provider_name is not None:
            result["provider_name"] = self.provider_name
        if self.underlying_exception is not None:
            result["underlying_exception"] = repr(self.underlying_exception)
        if self.details:
            result["details"] = self.details
        return result

    def safe_to_dict(self) -> dict[str, Any]:
        """Return a serialisable representation safe for external-facing output.

        Identical to ``to_dict()`` except that ``underlying_exception`` is
        always omitted.  Use this variant for HTTP error responses, audit logs
        shipped to external systems, or any output that crosses a trust
        boundary, to prevent accidental leakage of connection strings, ARNs,
        or other secrets that may appear in underlying exception messages.

        Sub-classes that override ``to_dict()`` should also override this
        method (or call ``super().safe_to_dict()`` and add their own fields
        without ``underlying_exception``).
        """
        result: dict[str, Any] = {
            "error_type": type(self).__name__,
            "message": self.message,
            "provider_type": self.provider_type,
            "is_retryable": self.is_retryable,
        }
        if self.provider_name is not None:
            result["provider_name"] = self.provider_name
        if self.details:
            result["details"] = self.details
        return result

    def __repr__(self) -> str:
        parts = [f"provider_type={self.provider_type!r}"]
        if self.provider_name:
            parts.append(f"provider_name={self.provider_name!r}")
        return f"{type(self).__name__}({self.message!r}, {', '.join(parts)})"


# ---------------------------------------------------------------------------
# Leaf exception types
# ---------------------------------------------------------------------------


class ProviderConfigError(ProviderError):
    """Raised when provider configuration is absent, incomplete, or invalid.

    Raise this at setup / initialisation time when the provider cannot be
    configured correctly.  Examples:

    * A required config key is missing (no ``region`` for AWS).
    * A config value fails schema validation.
    * A referenced secret or credentials file does not exist.

    This error is **not** retryable — the operator must fix the config.
    """

    _default_is_retryable: bool = False


class ProviderAuthError(ProviderError):
    """Raised when authentication or authorisation against the provider fails.

    Examples:

    * Expired or revoked credentials.
    * The caller's identity lacks a required permission (403 / AccessDenied).
    * Token refresh failed.

    This error is generally **not** retryable without operator intervention
    (credential rotation / IAM policy update), though short-lived token
    expiry may be retryable after a refresh cycle.
    """

    _default_is_retryable: bool = False


class ProviderQuotaError(ProviderError):
    """Raised when a provider quota, rate limit, or capacity ceiling is hit.

    Examples:

    * vCPU quota exhausted (AWS ``VcpuLimitExceeded``).
    * API calls throttled (AWS ``RequestLimitExceeded``, k8s 429).
    * Node pool capacity unavailable.

    Quota and rate-limit errors are treated as retryable: the quota may
    increase, the throttle window may expire, or a back-off retry may
    succeed.  Set ``is_retryable=False`` explicitly for hard quota ceilings
    that require operator action before any retry can succeed.
    """

    _default_is_retryable: bool = True


class ProviderTransientError(ProviderError):
    """Raised for retryable transient provider failures.

    Use this when the underlying provider signals that the error is
    temporary and a retry is likely to succeed:

    * HTTP 503 Service Unavailable.
    * Connection timeout or TCP reset.
    * AWS ``ServiceUnavailable`` or ``InternalError``.
    * k8s API server temporarily unreachable.

    The caller (retry middleware, resilience layer) should catch this
    class specifically to drive retry logic, or check ``is_retryable``
    for a provider-neutral approach.
    """

    _default_is_retryable: bool = True


class ProviderPermanentError(ProviderError):
    """Raised for non-retryable permanent provider failures.

    Use this when retrying will never help:

    * The requested resource does not exist (404).
    * The request is malformed (400 / ``ValidationError``).
    * A hard permission denial that cannot change without operator action.
    * Attempting to mutate an immutable resource.

    The caller should surface this directly to the end user or dead-letter
    queue rather than retrying.
    """

    _default_is_retryable: bool = False

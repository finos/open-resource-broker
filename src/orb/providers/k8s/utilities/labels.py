"""Kubernetes label-selector construction utilities.

Centralises label selector building so that all callsites use the same
validated, injection-safe helper instead of ad-hoc f-string interpolation.

Background
----------
Kubernetes label selectors are passed verbatim to the apiserver as query
parameters.  A label key or value that contains characters like ``=``,
``!``, ``(``, or URL-special characters can break the selector syntax and
in some configurations allow an operator-controlled string to influence
which pods are matched — a label-injection risk.

This module validates the *prefix*, *key*, and *value* components against
the Kubernetes label rules (derived from RFC 1123 / RFC 1035 with the
kubernetes-specific extensions) before assembling the selector string.
Any value that contains characters that cannot appear in a valid label is
rejected with :class:`K8sValidationError` rather than silently producing a
malformed selector.

Rules implemented
-----------------
* Label *prefix*: DNS subdomain — letters, digits, ``-``, ``_``, ``.``;
  must not start or end with a non-alphanumeric character; max 253 chars.
* Label *key* (name part): letters, digits, ``-``, ``_``, ``/``;
  max 63 chars; must not be empty.
* Label *value*: letters, digits, ``-``, ``_``, ``/``, ``.``;
  max 63 chars.  May be empty (Kubernetes allows empty label values).

The ``build_label_selector`` helper assembles a ``prefix/key=value``
selector string only after all three components pass validation.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

# DNS subdomain for the prefix component — allows dots and hyphens between
# alphanumeric segments.  Based on RFC 1123 + kubernetes label prefix rules.
_PREFIX_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-_.]*[a-zA-Z0-9])?$")
_PREFIX_MAX_LEN = 253

# Name / key part of a label (after the prefix/).
# Kubernetes allows: alphanumeric, -, _, .; max 63 chars.
_KEY_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-_.]*[a-zA-Z0-9])?$")
_KEY_MAX_LEN = 63

# Label value: same character class as name; empty is valid.
_VALUE_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-_.]*[a-zA-Z0-9])?$")
_VALUE_MAX_LEN = 63

# Namespace: RFC 1123 DNS label — lower-case letters, digits, hyphens.
# Must start and end with a letter or digit; max 63 chars.
_NAMESPACE_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$")
_NAMESPACE_MAX_LEN = 63


# ---------------------------------------------------------------------------
# Exceptions (avoid circular import — must not import from k8s_errors here
# because utilities is imported by handlers which import exceptions already)
# ---------------------------------------------------------------------------


class K8sValidationError(Exception):
    """Raised when a label key, value, or namespace fails validation."""


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _validate_prefix(prefix: str) -> None:
    """Raise :class:`K8sValidationError` when *prefix* is not a valid DNS subdomain."""
    if not prefix:
        raise K8sValidationError("label prefix must not be empty")
    if len(prefix) > _PREFIX_MAX_LEN:
        raise K8sValidationError(
            f"label prefix {prefix!r} exceeds max length {_PREFIX_MAX_LEN} (got {len(prefix)})"
        )
    if not _PREFIX_RE.match(prefix):
        raise K8sValidationError(
            f"label prefix {prefix!r} contains characters not permitted in a DNS subdomain.  "
            "Allowed: letters, digits, hyphens, underscores, dots."
        )


def _validate_key(key: str) -> None:
    """Raise :class:`K8sValidationError` when *key* is not a valid Kubernetes label name."""
    if not key:
        raise K8sValidationError("label key must not be empty")
    if len(key) > _KEY_MAX_LEN:
        raise K8sValidationError(
            f"label key {key!r} exceeds max length {_KEY_MAX_LEN} (got {len(key)})"
        )
    if not _KEY_RE.match(key):
        raise K8sValidationError(
            f"label key {key!r} contains characters not permitted in a Kubernetes label name.  "
            "Allowed: letters, digits, hyphens, underscores, dots."
        )


def _validate_value(value: str) -> None:
    """Raise :class:`K8sValidationError` when *value* is not a valid Kubernetes label value.

    Empty values are permitted — Kubernetes allows them in label selectors
    for equality-based matching (``key=``).
    """
    if not value:
        return  # empty is allowed
    if len(value) > _VALUE_MAX_LEN:
        raise K8sValidationError(
            f"label value {value!r} exceeds max length {_VALUE_MAX_LEN} (got {len(value)})"
        )
    if not _VALUE_RE.match(value):
        raise K8sValidationError(
            f"label value {value!r} contains characters not permitted in a Kubernetes label value.  "
            "Allowed: letters, digits, hyphens, underscores, dots."
        )


def validate_namespace(namespace: str) -> None:
    """Raise :class:`K8sValidationError` when *namespace* is not a valid RFC 1123 DNS label.

    Kubernetes namespaces must be lower-case RFC 1123 DNS labels:
    alphanumeric and hyphens, start and end with a letter or digit,
    maximum 63 characters.

    Args:
        namespace: The namespace string to validate.

    Raises:
        K8sValidationError: When the namespace is empty, too long, or
            contains disallowed characters.
    """
    if not namespace:
        raise K8sValidationError("namespace must not be empty")
    if len(namespace) > _NAMESPACE_MAX_LEN:
        raise K8sValidationError(
            f"namespace {namespace!r} exceeds max length {_NAMESPACE_MAX_LEN} (got {len(namespace)})"
        )
    if not _NAMESPACE_RE.match(namespace):
        raise K8sValidationError(
            f"namespace {namespace!r} is not a valid RFC 1123 DNS label.  "
            "Kubernetes namespaces must consist of lower-case letters, digits, and hyphens, "
            "and must start and end with a letter or digit."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_label_selector(prefix: str, key: str, value: str) -> str:
    """Build a ``prefix/key=value`` Kubernetes label selector string.

    Validates each component before assembly.  Refuses to build a selector
    that contains injection-capable characters by raising
    :class:`K8sValidationError` instead.

    Args:
        prefix: The DNS subdomain prefix (e.g. ``"orb.io"``).
        key: The label name (e.g. ``"managed"``).
        value: The label value (e.g. ``"true"``).  May be empty.

    Returns:
        A selector string of the form ``"prefix/key=value"`` suitable for
        use as the ``label_selector`` query parameter in Kubernetes API calls.

    Raises:
        K8sValidationError: When any component fails validation.

    Examples:
        >>> build_label_selector("orb.io", "managed", "true")
        'orb.io/managed=true'
        >>> build_label_selector("orb.io", "request-id", "abc-123")
        'orb.io/request-id=abc-123'
    """
    _validate_prefix(prefix)
    _validate_key(key)
    _validate_value(value)
    return f"{prefix}/{key}={value}"


__all__ = [
    "K8sValidationError",
    "build_label_selector",
    "validate_namespace",
]

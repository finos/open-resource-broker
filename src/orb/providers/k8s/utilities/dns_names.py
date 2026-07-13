"""Canonical DNS-1123 / RFC-1123 name validation for the Kubernetes provider.

Two distinct forms are defined and must not be conflated:

DNS-1123 label (``DNS_1123_LABEL_REGEX``)
    A single label component: lowercase alphanumeric characters and hyphens,
    must start and end with a letter or digit, maximum 63 characters.
    Used by Kubernetes for namespace names and service-account names.

DNS-1123 subdomain (``DNS_1123_SUBDOMAIN_REGEX``)
    One or more label components separated by dots, each satisfying the label
    rules above, maximum 253 characters total.
    Used by Kubernetes for label prefixes and kubeconfig context names.

Both regexes only validate the *character-set* and *structural* rules; callers
are responsible for applying the relevant length limit via
:func:`validate_dns_1123_label` or :func:`validate_dns_1123_subdomain`.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Canonical patterns
# ---------------------------------------------------------------------------

#: DNS-1123 label: lowercase alnum + hyphens, no leading/trailing hyphen.
#: Max length is 63 — enforced by :func:`validate_dns_1123_label`, not the
#: regex itself, so the pattern can be reused for length-independent checks.
DNS_1123_LABEL_REGEX: re.Pattern[str] = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

#: DNS-1123 subdomain: one or more dot-separated label segments.
#: Max length is 253 — enforced by :func:`validate_dns_1123_subdomain`.
DNS_1123_SUBDOMAIN_REGEX: re.Pattern[str] = re.compile(
    r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
)

#: Maximum number of characters in a DNS-1123 label (Kubernetes restriction).
DNS_1123_LABEL_MAX_LEN: int = 63

#: Maximum number of characters in a DNS-1123 subdomain.
DNS_1123_SUBDOMAIN_MAX_LEN: int = 253


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def validate_dns_1123_label(value: str, *, field_name: str = "value") -> None:
    """Raise :class:`ValueError` when *value* is not a valid DNS-1123 label.

    A DNS-1123 label must:

    * consist only of lowercase letters, digits, and hyphens,
    * start and end with a letter or digit,
    * not exceed 63 characters.

    Args:
        value: The string to validate.
        field_name: Human-readable name of the validated field, used in the
            error message.

    Raises:
        ValueError: When *value* is empty, too long, or contains disallowed
            characters.
    """
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > DNS_1123_LABEL_MAX_LEN:
        raise ValueError(
            f"{field_name} {value!r} exceeds the DNS-1123 label maximum of "
            f"{DNS_1123_LABEL_MAX_LEN} characters (got {len(value)})"
        )
    if not DNS_1123_LABEL_REGEX.match(value):
        raise ValueError(
            f"{field_name} {value!r} is not a valid DNS-1123 label.  "
            "Must consist of lowercase letters, digits, and hyphens; "
            "must start and end with a letter or digit."
        )


def validate_dns_1123_subdomain(value: str, *, field_name: str = "value") -> None:
    """Raise :class:`ValueError` when *value* is not a valid DNS-1123 subdomain.

    A DNS-1123 subdomain must:

    * consist of one or more dot-separated label segments, each satisfying
      the DNS-1123 label rules (lowercase alnum + hyphens, no leading/trailing
      hyphen),
    * not exceed 253 characters in total.

    Args:
        value: The string to validate.
        field_name: Human-readable name of the validated field.

    Raises:
        ValueError: When *value* is empty, too long, or contains disallowed
            characters.
    """
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > DNS_1123_SUBDOMAIN_MAX_LEN:
        raise ValueError(
            f"{field_name} {value!r} exceeds the DNS-1123 subdomain maximum of "
            f"{DNS_1123_SUBDOMAIN_MAX_LEN} characters (got {len(value)})"
        )
    if not DNS_1123_SUBDOMAIN_REGEX.match(value):
        raise ValueError(
            f"{field_name} {value!r} is not a valid DNS-1123 subdomain.  "
            "Must consist of lowercase letters, digits, hyphens, and dots; "
            "must start and end with a letter or digit; "
            "each dot-separated segment must not start or end with a hyphen."
        )


__all__ = [
    "DNS_1123_LABEL_MAX_LEN",
    "DNS_1123_LABEL_REGEX",
    "DNS_1123_SUBDOMAIN_MAX_LEN",
    "DNS_1123_SUBDOMAIN_REGEX",
    "validate_dns_1123_label",
    "validate_dns_1123_subdomain",
]

"""Provider-agnostic error classification.

Maps well-known cloud provider error-code strings to a :class:`DiagnosticCategory`
and builds a :class:`FulfilmentDiagnostic` whose summary is a *safe* category
template — never a raw provider error message that could leak identifiers.

This is the single classification primitive shared across the codebase: the AWS
fleet/spot/ASG classifier delegates to it, and the application failure path uses
it directly so a hard provider failure (e.g. ``InsufficientInstanceCapacity``)
is categorised consistently instead of being flattened to ``INTERNAL``.

The error-code sets are well-known cloud semantics expressed as plain strings;
the domain owns the *classification policy* while providers own the raw payload
shape they feed in.  Pure domain — no infra imports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from orb.domain.base.diagnostic import (
    DiagnosticCategory,
    FulfilmentDiagnostic,
    ProviderError,
)

# Well-known provider error codes grouped by diagnostic category.
CAPACITY_CODES = {
    "InsufficientInstanceCapacity",
    "SpotMaxPriceTooLow",
    "MaxSpotInstanceCountExceeded",
    "InstanceLimitExceeded",
}
AUTH_CODES = {"UnauthorizedOperation", "AccessDenied"}
VALIDATION_CODES = {"InvalidParameterValue", "InvalidLaunchTemplateName"}
RATE_LIMIT_CODES = {"Throttling", "RequestLimitExceeded"}

# Severity order for picking the winning category (most severe first). Mirrors
# the domain diagnostic severity so the classifier and merge agree.
_CATEGORY_SEVERITY: list[DiagnosticCategory] = [
    DiagnosticCategory.AUTH,
    DiagnosticCategory.VALIDATION,
    DiagnosticCategory.CAPACITY,
    DiagnosticCategory.RATE_LIMIT,
    DiagnosticCategory.INTERNAL,
    DiagnosticCategory.UNKNOWN,
]

# Safe, human-readable summary templates. These never embed a raw provider
# error message — only a coarse category and an error count.
_SUMMARY_TEMPLATES: dict[DiagnosticCategory, str] = {
    DiagnosticCategory.CAPACITY: "Insufficient capacity for {n} instance type(s)",
    DiagnosticCategory.AUTH: "Provider authorization denied",
    DiagnosticCategory.VALIDATION: "Provider request validation failed",
    DiagnosticCategory.RATE_LIMIT: "Provider throttled the request",
    DiagnosticCategory.INTERNAL: "Provider error",
    DiagnosticCategory.UNKNOWN: "Provider returned {n} error(s)",
}


def classify_error_code(code: str) -> DiagnosticCategory:
    """Map a single provider error code to a diagnostic category."""
    if code in AUTH_CODES:
        return DiagnosticCategory.AUTH
    if code in VALIDATION_CODES:
        return DiagnosticCategory.VALIDATION
    if code in CAPACITY_CODES:
        return DiagnosticCategory.CAPACITY
    if code in RATE_LIMIT_CODES:
        return DiagnosticCategory.RATE_LIMIT
    return DiagnosticCategory.UNKNOWN


def _most_severe(categories: list[DiagnosticCategory]) -> DiagnosticCategory:
    """Return the most-severe category from a list."""
    best = DiagnosticCategory.UNKNOWN
    best_rank = len(_CATEGORY_SEVERITY)
    for cat in categories:
        try:
            rank = _CATEGORY_SEVERITY.index(cat)
        except ValueError:
            rank = len(_CATEGORY_SEVERITY)
        if rank < best_rank:
            best_rank = rank
            best = cat
    return best


def classify_provider_errors(
    errors: list[dict[str, Any]], *, now: datetime
) -> FulfilmentDiagnostic:
    """Build a FulfilmentDiagnostic from a list of normalised provider error dicts.

    Args:
        errors: Normalised error dicts. Each may carry ``error_code``,
            ``error_message``, ``fleet_id``, ``az`` and ``instance_type``.
            The ``code`` / ``message`` / ``availability_zone`` aliases are also
            accepted.
        now: Observation timestamp.

    Returns:
        A FulfilmentDiagnostic with the most-severe category. An empty error
        list yields an UNKNOWN diagnostic (defensive — callers should only
        classify when there are errors).
    """
    provider_errors: list[ProviderError] = []
    categories: list[DiagnosticCategory] = []
    for err in errors or []:
        code = str(err.get("error_code") or err.get("code") or "Unknown")
        message = str(err.get("error_message") or err.get("message") or "")
        categories.append(classify_error_code(code))
        provider_errors.append(
            ProviderError(
                code=code,
                message=message,
                fleet_id=err.get("fleet_id"),
                az=err.get("az") or err.get("availability_zone"),
                instance_type=err.get("instance_type"),
            )
        )

    category = _most_severe(categories) if categories else DiagnosticCategory.UNKNOWN
    summary = _SUMMARY_TEMPLATES[category].format(n=len(provider_errors))

    return FulfilmentDiagnostic(
        category=category,
        summary=summary,
        provider_errors=provider_errors,
        occurred_at=now,
    )

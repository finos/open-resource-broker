"""AWS fleet/spot/ASG error classifier.

Translates the normalised AWS error dicts that the handlers already emit
(``{"error_code", "error_message", "fleet_id"?, "az"?, "instance_type"?}``)
into a provider-agnostic :class:`FulfilmentDiagnostic`.

The classification *core* (code -> category, most-severe-wins, provider-error
construction) lives in :mod:`orb.domain.base.error_classification` and is shared
with the application failure path so a hard AWS failure is categorised
identically wherever it is surfaced.  This module keeps only the AWS-branded
summary wording layered on top of that shared core.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic
from orb.domain.base.error_classification import (
    AUTH_CODES,
    CAPACITY_CODES,
    RATE_LIMIT_CODES,
    VALIDATION_CODES,
    classify_provider_errors,
)

# Re-exported for backward compatibility with existing importers.
__all__ = [
    "AUTH_CODES",
    "CAPACITY_CODES",
    "RATE_LIMIT_CODES",
    "VALIDATION_CODES",
    "classify_aws_errors",
]

# AWS-branded summary wording, layered over the shared classification core.
_AWS_SUMMARY_TEMPLATES: dict[DiagnosticCategory, str] = {
    DiagnosticCategory.CAPACITY: "Insufficient AWS capacity for {n} instance type(s)",
    DiagnosticCategory.AUTH: "AWS authorization denied",
    DiagnosticCategory.VALIDATION: "AWS request validation failed",
    DiagnosticCategory.RATE_LIMIT: "AWS throttled the request",
    DiagnosticCategory.INTERNAL: "AWS provider error",
    DiagnosticCategory.UNKNOWN: "AWS returned {n} error(s)",
}


def classify_aws_errors(errors: list[dict[str, Any]], *, now: datetime) -> FulfilmentDiagnostic:
    """Build a FulfilmentDiagnostic from a list of normalised AWS error dicts.

    Delegates classification to the shared domain classifier, then rewrites the
    summary with AWS-branded wording so operator-facing AWS diagnostics keep
    their familiar phrasing while the category/provider-error logic stays a
    single shared implementation.

    Args:
        errors: Normalised fleet/spot/ASG error dicts. Each may carry
            ``error_code``, ``error_message``, ``fleet_id``, ``az`` and
            ``instance_type``.
        now: Observation timestamp.

    Returns:
        A FulfilmentDiagnostic with the most-severe category. An empty error
        list yields an UNKNOWN diagnostic (defensive — callers should only
        classify when there are errors).
    """
    diag = classify_provider_errors(errors, now=now)
    summary = _AWS_SUMMARY_TEMPLATES[diag.category].format(n=len(diag.provider_errors))
    return diag.model_copy(update={"summary": summary})

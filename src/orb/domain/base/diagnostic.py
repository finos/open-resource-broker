"""Fulfilment diagnostic value objects — the *why* behind a request outcome.

``ProviderFulfilment`` answers "how much capacity was met"; this module answers
"why".  A ``FulfilmentDiagnostic`` carries a coarse category (capacity, auth,
validation, ...), a human-readable summary, and optional structured provider
errors.  It is provider-agnostic: AWS/K8s/etc. classifiers build one from their
own raw error payloads and the domain merges them on write.

Pure domain — no infra imports.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class DiagnosticCategory(str, Enum):
    """Coarse classification of *why* a request did not fully succeed.

    The order of the members is NOT the severity order — severity is defined
    explicitly by ``_SEVERITY_ORDER`` below so that reordering the enum for
    readability never silently changes merge behaviour.
    """

    CAPACITY = "capacity"
    VALIDATION = "validation"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    INTERNAL = "internal"
    DEADLINE = "deadline"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


# Severity order, most-severe first.  Used by ``FulfilmentDiagnostic.merge`` to
# decide which category "wins" when two diagnostics are combined.  Rationale:
# an auth failure across a fleet must never be masked by an unknown or a
# capacity blip — an operator needs to see the actionable, root-cause category.
_SEVERITY_ORDER: list[DiagnosticCategory] = [
    DiagnosticCategory.AUTH,
    DiagnosticCategory.VALIDATION,
    DiagnosticCategory.CAPACITY,
    DiagnosticCategory.RATE_LIMIT,
    DiagnosticCategory.DEADLINE,
    DiagnosticCategory.INTERNAL,
    DiagnosticCategory.CANCELLED,
    DiagnosticCategory.UNKNOWN,
]


def _severity_index(category: DiagnosticCategory) -> int:
    """Return the severity rank of a category (lower == more severe)."""
    try:
        return _SEVERITY_ORDER.index(category)
    except ValueError:
        # A category not listed is treated as least severe.
        return len(_SEVERITY_ORDER)


class ProviderError(BaseModel):
    """A single structured provider error observed while fulfilling a request.

    All location fields are optional because not every provider error is
    scoped to a fleet / AZ / instance type.
    """

    code: str
    message: str
    fleet_id: str | None = None
    az: str | None = None
    instance_type: str | None = None

    def _dedup_key(self) -> tuple[str, str | None, str | None, str | None]:
        """Key used to deduplicate provider errors when merging diagnostics."""
        return (self.code, self.fleet_id, self.az, self.instance_type)


class FulfilmentDiagnostic(BaseModel):
    """Structured explanation of why a request outcome is what it is.

    Attributes:
        category: The (most-severe) diagnostic category.
        summary: Short, safe-to-surface human-readable summary. Never contains
            raw provider error messages that could leak identifiers.
        detail: Optional longer description (e.g. a validation hint).
        provider_errors: Structured per-error records (may include raw codes;
            surfaced only through category-templated wire messages, never
            verbatim in HF messages).
        occurred_at: Timestamp of the observation.
    """

    category: DiagnosticCategory
    summary: str
    detail: str | None = None
    provider_errors: list[ProviderError] = Field(default_factory=list)
    occurred_at: datetime

    @classmethod
    def merge(cls, a: "FulfilmentDiagnostic", b: "FulfilmentDiagnostic") -> "FulfilmentDiagnostic":
        """Merge two diagnostics into one, most-severe-category-wins.

        - ``category``/``summary``/``detail`` come from the more-severe input.
        - ``provider_errors`` are concatenated and deduplicated by
          ``(code, fleet_id, az, instance_type)``.
        - ``occurred_at`` is the later of the two.
        """
        winner = a if _severity_index(a.category) <= _severity_index(b.category) else b

        seen: set[tuple[str, str | None, str | None, str | None]] = set()
        merged_errors: list[ProviderError] = []
        for err in [*a.provider_errors, *b.provider_errors]:
            key = err._dedup_key()
            if key in seen:
                continue
            seen.add(key)
            merged_errors.append(err)

        return cls(
            category=winner.category,
            summary=winner.summary,
            detail=winner.detail,
            provider_errors=merged_errors,
            occurred_at=max(a.occurred_at, b.occurred_at),
        )

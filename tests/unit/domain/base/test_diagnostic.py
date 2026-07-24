"""Unit tests for FulfilmentDiagnostic value objects."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from orb.domain.base.diagnostic import (
    DiagnosticCategory,
    FulfilmentDiagnostic,
    ProviderError,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)


def _diag(category: DiagnosticCategory, *, occurred_at=NOW, errors=None) -> FulfilmentDiagnostic:
    return FulfilmentDiagnostic(
        category=category,
        summary=f"{category.value} summary",
        provider_errors=errors or [],
        occurred_at=occurred_at,
    )


@pytest.mark.parametrize(
    ("a", "b", "winner"),
    [
        (DiagnosticCategory.AUTH, DiagnosticCategory.CAPACITY, DiagnosticCategory.AUTH),
        (DiagnosticCategory.CAPACITY, DiagnosticCategory.UNKNOWN, DiagnosticCategory.CAPACITY),
        (DiagnosticCategory.UNKNOWN, DiagnosticCategory.VALIDATION, DiagnosticCategory.VALIDATION),
        (DiagnosticCategory.CAPACITY, DiagnosticCategory.AUTH, DiagnosticCategory.AUTH),
        (DiagnosticCategory.RATE_LIMIT, DiagnosticCategory.CAPACITY, DiagnosticCategory.CAPACITY),
    ],
)
def test_merge_picks_most_severe_category(a, b, winner):
    """merge() keeps the most-severe category regardless of argument order."""
    merged = FulfilmentDiagnostic.merge(_diag(a), _diag(b))
    assert merged.category == winner


def test_merge_uses_latest_occurred_at():
    """merge() keeps the later occurred_at timestamp."""
    merged = FulfilmentDiagnostic.merge(
        _diag(DiagnosticCategory.CAPACITY, occurred_at=NOW),
        _diag(DiagnosticCategory.AUTH, occurred_at=LATER),
    )
    assert merged.occurred_at == LATER


def test_merge_deduplicates_provider_errors():
    """Provider errors are deduplicated by (code, fleet_id, az, instance_type)."""
    e1 = ProviderError(code="InsufficientInstanceCapacity", message="m", fleet_id="f1", az="a")
    e1_dup = ProviderError(
        code="InsufficientInstanceCapacity", message="other", fleet_id="f1", az="a"
    )
    e2 = ProviderError(code="InsufficientInstanceCapacity", message="m", fleet_id="f2", az="a")
    merged = FulfilmentDiagnostic.merge(
        _diag(DiagnosticCategory.CAPACITY, errors=[e1]),
        _diag(DiagnosticCategory.CAPACITY, errors=[e1_dup, e2]),
    )
    keys = {(e.code, e.fleet_id, e.az, e.instance_type) for e in merged.provider_errors}
    assert len(merged.provider_errors) == 2
    assert ("InsufficientInstanceCapacity", "f1", "a", None) in keys
    assert ("InsufficientInstanceCapacity", "f2", "a", None) in keys


def test_merge_summary_from_winning_category():
    """The summary follows the most-severe category's diagnostic."""
    merged = FulfilmentDiagnostic.merge(
        _diag(DiagnosticCategory.CAPACITY),
        _diag(DiagnosticCategory.AUTH),
    )
    assert merged.summary == "auth summary"


def test_round_trip_json():
    """Diagnostic serialises to/from JSON without loss."""
    diag = _diag(
        DiagnosticCategory.CAPACITY,
        errors=[ProviderError(code="X", message="y", instance_type="t3.large")],
    )
    restored = FulfilmentDiagnostic.model_validate_json(diag.model_dump_json())
    assert restored == diag

"""Unit tests for the AWS fleet error classifier."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from orb.domain.base.diagnostic import DiagnosticCategory
from orb.providers.aws.infrastructure.handlers.shared.error_classifier import (
    classify_aws_errors,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _err(code: str, **extra) -> dict:
    base = {"error_code": code, "error_message": f"{code} happened"}
    base.update(extra)
    return base


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("InsufficientInstanceCapacity", DiagnosticCategory.CAPACITY),
        ("SpotMaxPriceTooLow", DiagnosticCategory.CAPACITY),
        ("MaxSpotInstanceCountExceeded", DiagnosticCategory.CAPACITY),
        ("InstanceLimitExceeded", DiagnosticCategory.CAPACITY),
        ("UnauthorizedOperation", DiagnosticCategory.AUTH),
        ("AccessDenied", DiagnosticCategory.AUTH),
        ("InvalidParameterValue", DiagnosticCategory.VALIDATION),
        ("InvalidLaunchTemplateName", DiagnosticCategory.VALIDATION),
        ("Throttling", DiagnosticCategory.RATE_LIMIT),
        ("RequestLimitExceeded", DiagnosticCategory.RATE_LIMIT),
        ("SomethingWeird", DiagnosticCategory.UNKNOWN),
    ],
)
def test_single_code_classification(code, category):
    diag = classify_aws_errors([_err(code)], now=NOW)
    assert diag.category == category
    assert diag.occurred_at == NOW
    assert len(diag.provider_errors) == 1
    assert diag.provider_errors[0].code == code


def test_most_severe_wins_across_errors():
    """Auth beats capacity beats unknown when several codes are present."""
    diag = classify_aws_errors(
        [_err("InsufficientInstanceCapacity"), _err("UnauthorizedOperation"), _err("Weird")],
        now=NOW,
    )
    assert diag.category == DiagnosticCategory.AUTH
    assert len(diag.provider_errors) == 3


def test_location_fields_captured():
    diag = classify_aws_errors(
        [
            _err(
                "InsufficientInstanceCapacity",
                fleet_id="fleet-1",
                az="us-east-1a",
                instance_type="t3.large",
            )
        ],
        now=NOW,
    )
    pe = diag.provider_errors[0]
    assert pe.fleet_id == "fleet-1"
    assert pe.az == "us-east-1a"
    assert pe.instance_type == "t3.large"


def test_availability_zone_alias():
    diag = classify_aws_errors(
        [_err("InsufficientInstanceCapacity", availability_zone="us-west-2b")], now=NOW
    )
    assert diag.provider_errors[0].az == "us-west-2b"


def test_empty_list_yields_unknown():
    diag = classify_aws_errors([], now=NOW)
    assert diag.category == DiagnosticCategory.UNKNOWN
    assert diag.provider_errors == []


def test_capacity_summary_counts_errors():
    diag = classify_aws_errors(
        [_err("InsufficientInstanceCapacity"), _err("SpotMaxPriceTooLow")], now=NOW
    )
    assert diag.category == DiagnosticCategory.CAPACITY
    assert "2" in diag.summary

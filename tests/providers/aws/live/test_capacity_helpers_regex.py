"""Unit tests for the partial-fulfilment regex and assert_terminal_ok helper.

These tests run without AWS credentials and exercise:

1. ``_PARTIAL_PATTERN`` — ensures only canonical ORB phrases match and
   incidental ``X/Y`` substrings are rejected.
2. ``assert_terminal_ok`` — ensures a zero-target/zero-fulfilled response
   raises ``pytest.fail`` with an informative message.
"""

from __future__ import annotations

import pytest

from tests.providers.aws.live._capacity_helpers import (
    _PARTIAL_PATTERN,
    assert_terminal_ok,
)

# ---------------------------------------------------------------------------
# Positive cases: known ORB partial-fulfilment phrases
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [
        "Partially fulfilled: 2/5 instances",
        "Partially fulfilled: 0/4 instances",
        "partially fulfilled: 3/10 instances",  # case-insensitive
        "Instant fleet: 1/4 instance(s) running",
        "Instant fleet: 2/4 instance(s) running",
        "INSTANT FLEET: 3/8 instance(s) running",  # case-insensitive
        "Fleet fulfilled: 5/10",
        "fleet fulfilled: 1/1",  # edge: 1/1 after capacity tweak
        "Request failed: Partially fulfilled: 2/4 instances; spot capacity exhausted",
    ],
)
def test_partial_pattern_matches_canonical_phrases(message: str) -> None:
    """_PARTIAL_PATTERN must match known ORB partial-fulfilment strings."""
    assert _PARTIAL_PATTERN.search(message) is not None, (
        f"Expected _PARTIAL_PATTERN to match {message!r}"
    )


# ---------------------------------------------------------------------------
# Negative cases: incidental "X/Y" substrings that must NOT match
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [
        "worker 1/3 completed",
        "step 2/5 of provisioning",
        "retry 1/3",
        "batch 3/10 processed",
        "Progress: 4/7",
        "instances 2/6",  # no canonical prefix
        "some error: 0/4",  # no canonical prefix
        "fulfilled 3/5",  # 'fulfilled' alone, no 'Partially'/'Fleet'/'Instant fleet'
    ],
)
def test_partial_pattern_rejects_incidental_fractions(message: str) -> None:
    """_PARTIAL_PATTERN must NOT match bare X/Y fractions without a canonical prefix."""
    assert _PARTIAL_PATTERN.search(message) is None, (
        f"Expected _PARTIAL_PATTERN NOT to match {message!r}"
    )


# ---------------------------------------------------------------------------
# assert_terminal_ok: zero-target/zero-fulfilled guard
# ---------------------------------------------------------------------------


def _make_status_response(
    status: str,
    target_units: int | None = None,
    fulfilled_units: int | None = None,
    message: str = "",
    machine_ids: list[str] | None = None,
    request_id: str = "req-test-001",
) -> dict:
    """Build a minimal getRequestStatus-shaped response dict for testing."""
    req: dict = {"status": status, "request_id": request_id, "message": message}
    if target_units is not None:
        req["target_units"] = target_units
    if fulfilled_units is not None:
        req["fulfilled_units"] = fulfilled_units
    if machine_ids is not None:
        req["machine_ids"] = machine_ids
    return {"requests": [req]}


@pytest.mark.unit
def test_zero_target_zero_fulfilled_fails() -> None:
    """assert_terminal_ok must pytest.fail when both target and fulfilled are 0.

    This indicates a scheduler bug (no capacity was ever issued) rather than a
    real AWS partial-fulfilment.  The fail message must include the request_id.
    """
    response = _make_status_response(
        status="complete",
        target_units=0,
        fulfilled_units=0,
        request_id="req-buggy-001",
    )
    with pytest.raises(pytest.fail.Exception) as exc_info:
        assert_terminal_ok(response, requested_count=4)

    assert "req-buggy-001" in str(exc_info.value)
    assert "scheduler bug" in str(exc_info.value).lower()


@pytest.mark.unit
def test_zero_target_zero_fulfilled_with_empty_machine_list_fails() -> None:
    """Zero-target response derived from empty machine_ids list also fails.

    When target_units/fulfilled_units are absent from the response but
    machine_ids is empty, both values fall back to 0 and the guard fires.
    """
    response = _make_status_response(
        status="complete",
        machine_ids=[],
        request_id="req-empty-001",
    )
    with pytest.raises(pytest.fail.Exception) as exc_info:
        assert_terminal_ok(response, requested_count=0)

    assert "req-empty-001" in str(exc_info.value)


@pytest.mark.unit
def test_nonzero_complete_does_not_trigger_zero_guard() -> None:
    """A normal complete response with matching counts must not hit the 0/0 guard."""
    response = _make_status_response(
        status="complete",
        target_units=2,
        fulfilled_units=2,
    )
    # Must not raise
    assert_terminal_ok(response, requested_count=2)


@pytest.mark.unit
def test_partial_with_nonzero_units_does_not_trigger_zero_guard() -> None:
    """A legitimate partial response (>0 fulfilled) must not hit the 0/0 guard."""
    response = _make_status_response(
        status="complete_with_error",
        target_units=4,
        fulfilled_units=2,
        message="Partially fulfilled: 2/4 instances",
    )
    # Must not raise
    assert_terminal_ok(response, requested_count=4)

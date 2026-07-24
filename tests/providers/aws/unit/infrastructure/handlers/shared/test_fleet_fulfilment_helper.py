"""Unit tests for the compute_capacity_based_fulfilment shared helper."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.providers.aws.infrastructure.handlers.shared.fleet_fulfilment import (
    aggregate_fleet_fulfilment,
    compute_capacity_based_fulfilment,
)


def _capacity_diag(summary: str = "shortfall") -> FulfilmentDiagnostic:
    return FulfilmentDiagnostic(
        category=DiagnosticCategory.CAPACITY,
        summary=summary,
        occurred_at=datetime.now(timezone.utc),
    )


def _contributor(
    state: str,
    *,
    target_units: int | None = None,
    fulfilled_units: int | None = None,
    running_count: int | None = None,
    pending_count: int | None = None,
    failed_count: int | None = None,
    final: bool = False,
    diagnostic: FulfilmentDiagnostic | None = None,
) -> ProviderFulfilment:
    return ProviderFulfilment(
        state=state,  # type: ignore[arg-type]
        message="contributor",
        target_units=target_units,
        fulfilled_units=fulfilled_units,
        running_count=running_count,
        pending_count=pending_count,
        failed_count=failed_count,
        final=final,
        diagnostic=diagnostic,
    )


@pytest.mark.unit
def test_fully_fulfilled_returns_fulfilled_state():
    """target_capacity met, all running, no pending/failed → fulfilled."""
    result = compute_capacity_based_fulfilment(
        target_capacity=10,
        fulfilled_capacity=10.0,
        running_count=10,
        pending_count=0,
        failed_count=0,
        provider_label="Fleet",
    )
    assert isinstance(result, ProviderFulfilment)
    assert result.state == "fulfilled"
    assert result.running_count == 10
    assert result.pending_count == 0
    assert result.failed_count == 0
    assert result.fulfilled_units == 10
    assert result.target_units == 10


@pytest.mark.unit
def test_in_progress_when_pending_present():
    """fulfilled_capacity < target or pending instances → in_progress."""
    result = compute_capacity_based_fulfilment(
        target_capacity=10,
        fulfilled_capacity=4.0,
        running_count=4,
        pending_count=6,
        failed_count=0,
        provider_label="Fleet",
    )
    assert result.state == "in_progress"
    assert result.running_count == 4
    assert result.pending_count == 6
    assert result.failed_count == 0


@pytest.mark.unit
def test_partial_when_failed_present_no_pending():
    """failed > 0 AND running == 0 AND pending == 0 → failed state (not partial)."""
    # The implementation: failed>0, running==0, pending==0 → "failed" branch.
    # When there is a mix (some running, some failed, no pending) → in_progress/else branch.
    # Test the pure-failed case first.
    result = compute_capacity_based_fulfilment(
        target_capacity=5,
        fulfilled_capacity=0.0,
        running_count=0,
        pending_count=0,
        failed_count=5,
        provider_label="Fleet",
    )
    assert result.state == "failed"
    assert result.failed_count == 5
    assert result.running_count == 0


@pytest.mark.unit
def test_in_progress_when_some_running_some_failed_no_pending():
    """Some running + some failed + no pending → in_progress (else branch)."""
    result = compute_capacity_based_fulfilment(
        target_capacity=10,
        fulfilled_capacity=3.0,
        running_count=3,
        pending_count=0,
        failed_count=2,
        provider_label="Spot Fleet",
    )
    # failed_count > 0 but running_count > 0 → does NOT hit the pure-failed branch
    assert result.state == "in_progress"


@pytest.mark.unit
def test_provider_label_appears_in_message():
    """The provider_label value must appear verbatim in the returned message."""
    result = compute_capacity_based_fulfilment(
        target_capacity=2,
        fulfilled_capacity=2.0,
        running_count=2,
        pending_count=0,
        failed_count=0,
        provider_label="Spot Fleet",
    )
    assert "Spot Fleet" in result.message


@pytest.mark.unit
def test_zero_target_capacity_handles_gracefully():
    """target_capacity=0 with fulfilled_capacity=0 must not raise."""
    # fulfilled_capacity (0.0) >= target_capacity (0) is True, and pending==0,
    # failed==0 → fulfilled branch.
    result = compute_capacity_based_fulfilment(
        target_capacity=0,
        fulfilled_capacity=0.0,
        running_count=0,
        pending_count=0,
        failed_count=0,
        provider_label="Fleet",
    )
    assert result.state == "fulfilled"
    assert result.target_units == 0
    assert result.fulfilled_units == 0


@pytest.mark.unit
def test_none_target_capacity_uses_fulfilled_as_target():
    """When target_capacity is None, fulfilled_capacity is used as the target."""
    result = compute_capacity_based_fulfilment(
        target_capacity=None,
        fulfilled_capacity=4.0,
        running_count=4,
        pending_count=0,
        failed_count=0,
        provider_label="Fleet",
    )
    # target_capacity is None → fleet_fully_fulfilled is False → in_progress/else branch.
    assert result.state == "in_progress"
    assert result.target_units == 4  # derived from int(fulfilled_capacity)


# ---------------------------------------------------------------------------
# aggregate_fleet_fulfilment — multi-fleet roll-up contract.
#
# The two invariants under test:
#   1. target_units == requested_count (a single known whole-request target),
#      NEVER the sum of per-fleet target_units (per-fleet handlers fall back to
#      the full request total when AWS omits capacity, so summing over-counts).
#   2. diagnostic is merged ONLY for genuine shortfall/terminal states
#      (partial / failed).  in_progress and fulfilled aggregates carry
#      diagnostic=None, matching the single-fleet path.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_aggregate_case_a_two_in_progress_no_diag_target_is_requested_count():
    """(a) 2 fleets in_progress → in_progress, final=False, diag=None, counts summed."""
    contributors = [
        _contributor("in_progress", fulfilled_units=2, running_count=2, pending_count=3),
        _contributor("in_progress", fulfilled_units=1, running_count=1, pending_count=4),
    ]
    result = aggregate_fleet_fulfilment(
        state="in_progress",
        message="still provisioning",
        final=False,
        contributors=contributors,
        requested_count=10,
    )
    assert result.state == "in_progress"
    assert result.final is False
    assert result.diagnostic is None
    assert result.target_units == 10  # requested_count, NOT summed
    assert result.fulfilled_units == 3
    assert result.running_count == 3
    assert result.pending_count == 7
    assert result.failed_count is None  # all contributors None → stays None


@pytest.mark.unit
def test_aggregate_case_b_partial_plus_in_progress_gates_diag_out():
    """(b) 1 partial(final, CAPACITY) + 1 in_progress → combined in_progress, diag gated out.

    This is the exact defect: a still-progressing IN_PROGRESS aggregate must NOT
    carry a contributor's CAPACITY shortfall diagnostic.
    """
    contributors = [
        _contributor(
            "partial",
            fulfilled_units=1,
            running_count=1,
            final=True,
            diagnostic=_capacity_diag("1/3"),
        ),
        _contributor("in_progress", fulfilled_units=0, running_count=0, pending_count=2),
    ]
    # Caller precedence (ec2_fleet): any in_progress wins → combined in_progress.
    result = aggregate_fleet_fulfilment(
        state="in_progress",
        message="one or more fleets still provisioning",
        final=False,
        contributors=contributors,
        requested_count=10,
    )
    assert result.state == "in_progress"
    assert result.final is False
    assert result.diagnostic is None  # gated out despite a CAPACITY contributor
    assert result.target_units == 10


@pytest.mark.unit
def test_aggregate_case_c_two_partial_final_merges_diag():
    """(c) 2 partial (both final, CAPACITY) → partial, final=True, merged CAPACITY diag."""
    contributors = [
        _contributor(
            "partial",
            fulfilled_units=1,
            running_count=1,
            final=True,
            diagnostic=_capacity_diag("1/3"),
        ),
        _contributor(
            "partial",
            fulfilled_units=2,
            running_count=2,
            final=True,
            diagnostic=_capacity_diag("2/4"),
        ),
    ]
    result = aggregate_fleet_fulfilment(
        state="partial",
        message="partially fulfilled",
        final=True,
        contributors=contributors,
        requested_count=7,
    )
    assert result.state == "partial"
    assert result.final is True
    assert result.diagnostic is not None
    assert result.diagnostic.category == DiagnosticCategory.CAPACITY
    assert result.target_units == 7  # requested_count (not 3 + 4)
    assert result.fulfilled_units == 3
    assert result.running_count == 3


@pytest.mark.unit
def test_aggregate_case_d_failed_plus_partial_merges_diag():
    """(d) 1 failed + 1 partial → combined failed (caller precedence), diag merged."""
    contributors = [
        _contributor(
            "failed",
            fulfilled_units=0,
            running_count=0,
            failed_count=3,
            diagnostic=_capacity_diag("failed fleet"),
        ),
        _contributor(
            "partial",
            fulfilled_units=1,
            running_count=1,
            final=True,
            diagnostic=_capacity_diag("1/2"),
        ),
    ]
    # Caller precedence: no in_progress, any failed → combined failed.
    result = aggregate_fleet_fulfilment(
        state="failed",
        message="one or more fleets failed",
        final=False,
        contributors=contributors,
        requested_count=5,
    )
    assert result.state == "failed"
    assert result.diagnostic is not None  # failed is a shortfall state → merged
    assert result.target_units == 5
    assert result.fulfilled_units == 1
    assert result.failed_count == 3


@pytest.mark.unit
def test_aggregate_case_e_all_fulfilled_no_diag():
    """(e) all fulfilled → fulfilled, diag=None, target=requested_count, counts summed."""
    contributors = [
        _contributor("fulfilled", fulfilled_units=4, running_count=4),
        _contributor("fulfilled", fulfilled_units=6, running_count=6),
    ]
    result = aggregate_fleet_fulfilment(
        state="fulfilled",
        message="all fleets fulfilled",
        final=False,
        contributors=contributors,
        requested_count=10,
    )
    assert result.state == "fulfilled"
    assert result.diagnostic is None
    assert result.target_units == 10
    assert result.fulfilled_units == 10
    assert result.running_count == 10


@pytest.mark.unit
def test_aggregate_fulfilled_ignores_contributor_diagnostic():
    """A stray diagnostic on a fulfilled aggregate is dropped (nothing to explain)."""
    contributors = [
        _contributor("fulfilled", fulfilled_units=5, running_count=5, diagnostic=_capacity_diag()),
        _contributor("fulfilled", fulfilled_units=5, running_count=5),
    ]
    result = aggregate_fleet_fulfilment(
        state="fulfilled",
        message="all fleets fulfilled",
        final=False,
        contributors=contributors,
        requested_count=10,
    )
    assert result.state == "fulfilled"
    assert result.diagnostic is None

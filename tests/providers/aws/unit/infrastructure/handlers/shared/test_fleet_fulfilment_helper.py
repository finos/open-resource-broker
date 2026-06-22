"""Unit tests for the compute_capacity_based_fulfilment shared helper."""

from __future__ import annotations

import pytest

from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.providers.aws.infrastructure.handlers.shared.fleet_fulfilment import (
    compute_capacity_based_fulfilment,
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

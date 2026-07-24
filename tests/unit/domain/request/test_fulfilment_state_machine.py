"""Unit tests for the FulfilmentStateMachine — the single status-write authority."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from orb.domain.base.diagnostic import DiagnosticCategory
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.request.exceptions import InvalidRequestStateError
from orb.domain.request.fulfilment_state_machine import (
    FulfilmentEvent,
    FulfilmentStateMachine,
)
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
GRACE = 300


def _sm() -> FulfilmentStateMachine:
    return FulfilmentStateMachine(grace_period_seconds=GRACE)


def _new(status: RequestStatus = RequestStatus.PENDING, **overrides) -> Request:
    r = Request.create_new_request(RequestType.ACQUIRE, "tmpl", 3, "aws")
    updates = {"status": status}
    updates.update(overrides)
    return r.model_copy(update=updates)


def _new_return(status: RequestStatus = RequestStatus.PENDING, **overrides) -> Request:
    r = Request.create_return_request(
        machine_ids=["i-1", "i-2", "i-3"],
        provider_type="aws",
        provider_name="aws-1",
        provider_api="EC2Fleet",
    )
    updates = {"status": status}
    updates.update(overrides)
    return r.model_copy(update=updates)


def _verdict(state: str, msg: str = "m") -> ProviderFulfilment:
    return ProviderFulfilment(state=state, message=msg)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Basic event mapping
# ---------------------------------------------------------------------------


def test_start_moves_pending_to_in_progress_and_sets_deadline():
    r = _sm().apply(_new(), FulfilmentEvent.START, now=NOW)
    assert r.status == RequestStatus.IN_PROGRESS
    assert r.started_at == NOW
    assert r.deadline_at == NOW + timedelta(seconds=GRACE)
    assert r.last_transition_at == NOW


def test_resources_created_moves_to_acquiring():
    r = _sm().apply(_new(), FulfilmentEvent.RESOURCES_CREATED, now=NOW)
    assert r.status == RequestStatus.ACQUIRING
    assert r.deadline_at == NOW + timedelta(seconds=GRACE)


def test_fulfilled_verdict_completes():
    r = _sm().apply(
        _new(RequestStatus.IN_PROGRESS),
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW,
        fulfilment=_verdict("fulfilled"),
    )
    assert r.status == RequestStatus.COMPLETED
    assert r.completed_at == NOW


def test_failed_verdict_fails():
    r = _sm().apply(
        _new(RequestStatus.IN_PROGRESS),
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW,
        fulfilment=_verdict("failed"),
    )
    assert r.status == RequestStatus.FAILED


def test_cancel_event_cancels_with_diagnostic():
    r = _sm().apply(_new(RequestStatus.IN_PROGRESS), FulfilmentEvent.CANCEL, now=NOW, reason="stop")
    assert r.status == RequestStatus.CANCELLED
    assert r.fulfilment_diagnostic is not None
    assert r.fulfilment_diagnostic.category == DiagnosticCategory.CANCELLED


# ---------------------------------------------------------------------------
# Core new rule: partial within/after deadline
# ---------------------------------------------------------------------------


def test_partial_within_deadline_is_partial_pending():
    start = _sm().apply(_new(), FulfilmentEvent.START, now=NOW)
    r = _sm().apply(
        start,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=10),
        fulfilment=_verdict("partial"),
    )
    assert r.status == RequestStatus.PARTIAL_PENDING
    assert r.partial_since == NOW + timedelta(seconds=10)
    assert not r.status.is_terminal()


def test_partial_after_deadline_is_terminal_partial():
    start = _sm().apply(_new(successful_count=2), FulfilmentEvent.START, now=NOW)
    # A partial verdict arriving after the deadline resolves to terminal PARTIAL
    # via the deadline sweep (successful_count > 0).
    r = _sm().apply(
        start,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=GRACE + 1),
        fulfilment=_verdict("partial"),
    )
    assert r.status == RequestStatus.PARTIAL
    assert r.status.is_terminal()


def test_partial_pending_completes_when_capacity_arrives():
    start = _sm().apply(_new(), FulfilmentEvent.START, now=NOW)
    pending = _sm().apply(
        start,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=10),
        fulfilment=_verdict("partial"),
    )
    completed = _sm().apply(
        pending,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=20),
        fulfilment=_verdict("fulfilled"),
    )
    assert completed.status == RequestStatus.COMPLETED


# ---------------------------------------------------------------------------
# Deadline sweep
# ---------------------------------------------------------------------------


def test_evaluate_deadline_before_deadline_noop():
    r = _new(RequestStatus.IN_PROGRESS, deadline_at=NOW + timedelta(seconds=100), started_at=NOW)
    out = _sm().evaluate_deadline(r, now=NOW + timedelta(seconds=50))
    assert out is r


def test_evaluate_deadline_expired_with_capacity_is_partial():
    r = _new(
        RequestStatus.ACQUIRING,
        deadline_at=NOW,
        started_at=NOW - timedelta(seconds=GRACE),
        successful_count=1,
    )
    out = _sm().evaluate_deadline(r, now=NOW + timedelta(seconds=1))
    assert out.status == RequestStatus.PARTIAL
    assert out.fulfilment_diagnostic.category == DiagnosticCategory.DEADLINE


def test_evaluate_deadline_expired_zero_capacity_is_timeout():
    r = _new(
        RequestStatus.ACQUIRING,
        deadline_at=NOW,
        started_at=NOW - timedelta(seconds=GRACE),
        successful_count=0,
    )
    out = _sm().evaluate_deadline(r, now=NOW + timedelta(seconds=1))
    assert out.status == RequestStatus.TIMEOUT
    assert out.fulfilment_diagnostic.category == DiagnosticCategory.DEADLINE


def test_evaluate_deadline_terminal_noop():
    r = _new(RequestStatus.COMPLETED, deadline_at=NOW - timedelta(seconds=1))
    out = _sm().evaluate_deadline(r, now=NOW)
    assert out is r


def test_evaluate_deadline_is_idempotent():
    r = _new(
        RequestStatus.ACQUIRING,
        deadline_at=NOW,
        started_at=NOW - timedelta(seconds=GRACE),
        successful_count=0,
    )
    once = _sm().evaluate_deadline(r, now=NOW + timedelta(seconds=1))
    twice = _sm().evaluate_deadline(once, now=NOW + timedelta(seconds=2))
    assert twice is once


# ---------------------------------------------------------------------------
# Authority / override rules
# ---------------------------------------------------------------------------


def test_cancel_overrides_expired_deadline():
    r = _new(
        RequestStatus.IN_PROGRESS,
        deadline_at=NOW - timedelta(seconds=1),
        started_at=NOW - timedelta(seconds=GRACE),
    )
    out = _sm().apply(r, FulfilmentEvent.CANCEL, now=NOW, reason="operator")
    assert out.status == RequestStatus.CANCELLED


def test_fail_overrides_expired_deadline():
    r = _new(
        RequestStatus.IN_PROGRESS,
        deadline_at=NOW - timedelta(seconds=1),
        started_at=NOW - timedelta(seconds=GRACE),
    )
    out = _sm().apply(r, FulfilmentEvent.FAIL, now=NOW, message="boom")
    assert out.status == RequestStatus.FAILED


def test_deadline_wins_over_late_provider_verdict():
    r = _new(
        RequestStatus.IN_PROGRESS,
        deadline_at=NOW - timedelta(seconds=1),
        started_at=NOW - timedelta(seconds=GRACE),
        successful_count=0,
    )
    out = _sm().apply(
        r, FulfilmentEvent.PROVIDER_VERDICT, now=NOW, fulfilment=_verdict("fulfilled")
    )
    assert out.status == RequestStatus.TIMEOUT


# ---------------------------------------------------------------------------
# Terminal guard + back-compat
# ---------------------------------------------------------------------------


def test_terminal_completed_rejects_further_verdict():
    r = _new(RequestStatus.COMPLETED)
    with pytest.raises(InvalidRequestStateError):
        _sm().apply(
            r, FulfilmentEvent.PROVIDER_VERDICT, now=NOW, fulfilment=_verdict("in_progress")
        )


def test_partial_to_completed_backcompat_upgrade():
    r = _new(RequestStatus.PARTIAL)
    out = _sm().apply(
        r, FulfilmentEvent.PROVIDER_VERDICT, now=NOW, fulfilment=_verdict("fulfilled")
    )
    assert out.status == RequestStatus.COMPLETED


def test_idempotent_reapplication_is_noop():
    r = _new(RequestStatus.IN_PROGRESS, deadline_at=NOW + timedelta(seconds=100), started_at=NOW)
    out = _sm().apply(
        r, FulfilmentEvent.PROVIDER_VERDICT, now=NOW, fulfilment=_verdict("in_progress")
    )
    assert out is r


def test_deadline_sweep_event_only_evaluates_deadline():
    r = _new(
        RequestStatus.ACQUIRING,
        deadline_at=NOW,
        started_at=NOW - timedelta(seconds=GRACE),
        successful_count=0,
    )
    out = _sm().apply(r, FulfilmentEvent.DEADLINE_SWEEP, now=NOW + timedelta(seconds=1))
    assert out.status == RequestStatus.TIMEOUT


def test_provider_verdict_requires_fulfilment():
    with pytest.raises(ValueError):
        _sm().apply(_new(RequestStatus.IN_PROGRESS), FulfilmentEvent.PROVIDER_VERDICT, now=NOW)


# ---------------------------------------------------------------------------
# Exhaustive-ish transition matrix: no unexpected exceptions for legal moves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["fulfilled", "in_progress", "failed", "partial"])
def test_verdict_from_in_progress_never_raises(state):
    r = _sm().apply(_new(), FulfilmentEvent.START, now=NOW)
    out = _sm().apply(
        r,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=1),
        fulfilment=_verdict(state),
    )
    assert isinstance(out, Request)


# ---------------------------------------------------------------------------
# ROOT CAUSE 1 — deadline sweep classification uses effective_count
# ---------------------------------------------------------------------------


def test_deadline_sweep_full_capacity_completes_not_partial():
    """3/3 acquired (via machine_ids) but no 'fulfilled' verdict before the
    deadline must resolve to COMPLETED, not a failure-like PARTIAL."""
    r = _new(
        RequestStatus.ACQUIRING,
        deadline_at=NOW,
        started_at=NOW - timedelta(seconds=GRACE),
        requested_count=3,
        successful_count=0,  # lags — not healed for ACQUIRING
        machine_ids=["i-1", "i-2", "i-3"],
    )
    out = _sm().evaluate_deadline(r, now=NOW + timedelta(seconds=1))
    assert out.status == RequestStatus.COMPLETED
    # No DEADLINE diagnostic — nothing fell short.
    assert out.fulfilment_diagnostic is None


def test_deadline_sweep_uses_machine_ids_when_successful_count_lags():
    """machine_ids populated (2/3), successful_count still 0, deadline passed →
    PARTIAL (not TIMEOUT)."""
    r = _new(
        RequestStatus.ACQUIRING,
        deadline_at=NOW,
        started_at=NOW - timedelta(seconds=GRACE),
        requested_count=3,
        successful_count=0,
        machine_ids=["i-1", "i-2"],
    )
    out = _sm().evaluate_deadline(r, now=NOW + timedelta(seconds=1))
    assert out.status == RequestStatus.PARTIAL
    assert out.status.is_terminal()
    assert out.fulfilment_diagnostic.category == DiagnosticCategory.DEADLINE


def test_deadline_sweep_no_capacity_at_all_is_timeout():
    """No machine_ids and successful_count 0 → TIMEOUT."""
    r = _new(
        RequestStatus.ACQUIRING,
        deadline_at=NOW,
        started_at=NOW - timedelta(seconds=GRACE),
        requested_count=3,
        successful_count=0,
        machine_ids=[],
    )
    out = _sm().evaluate_deadline(r, now=NOW + timedelta(seconds=1))
    assert out.status == RequestStatus.TIMEOUT


# ---------------------------------------------------------------------------
# ROOT CAUSE 3 — RETURN requests keep terminal PARTIAL semantics
# ---------------------------------------------------------------------------


def test_return_partial_verdict_is_terminal_partial_within_deadline():
    """A RETURN request receiving a 'partial' verdict while still within its
    deadline must land on terminal PARTIAL, NOT the ACQUIRE holding state
    PARTIAL_PENDING."""
    started = _sm().apply(_new_return(), FulfilmentEvent.START, now=NOW)
    out = _sm().apply(
        started,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=10),  # well within the grace period
        fulfilment=_verdict("partial"),
    )
    assert out.status == RequestStatus.PARTIAL
    assert out.status.is_terminal()


def test_return_request_not_swept_by_deadline():
    """A RETURN request that is past its deadline must not be swept to
    TIMEOUT/PARTIAL by the deadline machinery — deadline semantics are
    ACQUIRE-only."""
    r = _new_return(
        RequestStatus.IN_PROGRESS,
        deadline_at=NOW,
        started_at=NOW - timedelta(seconds=GRACE),
        successful_count=0,
    )
    out = _sm().evaluate_deadline(r, now=NOW + timedelta(seconds=1))
    assert out is r
    assert out.status == RequestStatus.IN_PROGRESS


def test_acquire_partial_still_uses_partial_pending_within_deadline():
    """Regression guard: the ACQUIRE holding-state behaviour is unchanged."""
    started = _sm().apply(_new(), FulfilmentEvent.START, now=NOW)
    out = _sm().apply(
        started,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=10),
        fulfilment=_verdict("partial"),
    )
    assert out.status == RequestStatus.PARTIAL_PENDING


# ---------------------------------------------------------------------------
# Final partial resolves to terminal PARTIAL immediately (not held in limbo)
#
# Finality is signalled EXCLUSIVELY by ProviderFulfilment.final — never
# inferred from pending_count.  Synchronous providers (AWS RunInstances /
# instant fleet / MicroVM) set final=True; asynchronous providers (Kubernetes)
# leave it False so a transient partial can heal via PARTIAL_PENDING.
# ---------------------------------------------------------------------------


def _final_partial(msg: str = "2/3") -> ProviderFulfilment:
    """A provider-declared FINAL partial verdict (synchronous AWS launch):
    the launch API has settled, so no more capacity is coming (e.g. RunInstances
    returned 2 running, 1 failed, 0 pending, and marks the verdict final)."""
    return ProviderFulfilment(
        state="partial",  # type: ignore[arg-type]
        message=msg,
        running_count=2,
        pending_count=0,
        failed_count=1,
        final=True,
    )


def _nonfinal_partial(msg: str = "2/3") -> ProviderFulfilment:
    """A still-gathering partial verdict (final defaults False): the request may
    yet complete before the deadline, so it belongs in PARTIAL_PENDING."""
    return ProviderFulfilment(
        state="partial",  # type: ignore[arg-type]
        message=msg,
        running_count=2,
        pending_count=1,
        failed_count=0,
    )


def _k8s_transient_partial(msg: str = "2/3 ready") -> ProviderFulfilment:
    """A Kubernetes-shaped partial verdict: the resolvers emit ``partial`` ONLY
    when ``pending_count == 0`` (they return ``in_progress`` while pods are still
    pending), yet that zero is *transient* — the pod list lags the controller's
    reconciliation intent (e.g. a StatefulSet OrderedReady rollout between
    pod-N Ready and pod-(N+1) creation shows 2 ready / 0 pending / 0 failed for
    a requested_count of 3).  ``final`` is left False so it can heal."""
    return ProviderFulfilment(
        state="partial",  # type: ignore[arg-type]
        message=msg,
        running_count=2,
        pending_count=0,
        failed_count=0,
        final=False,
    )


def test_final_partial_within_deadline_is_terminal_partial():
    """A provider-declared FINAL partial (final=True) within the deadline must
    resolve to terminal PARTIAL immediately — not sit in PARTIAL_PENDING burning
    the grace period waiting for a 'fulfilled' verdict that can never arrive.
    This is the AWS synchronous settled-shortfall case."""
    started = _sm().apply(_new(), FulfilmentEvent.START, now=NOW)
    out = _sm().apply(
        started,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=10),  # well within the grace period
        fulfilment=_final_partial(),
    )
    assert out.status == RequestStatus.PARTIAL
    assert out.status.is_terminal()


def test_k8s_transient_partial_within_deadline_is_partial_pending():
    """Regression: a Kubernetes-shaped partial (pending_count == 0,
    failed_count == 0, ready < requested, final=False) within the deadline must
    park in PARTIAL_PENDING — NOT terminalise.  Inferring finality from
    pending_count == 0 stranded k8s StatefulSet OrderedReady rollouts at N/M
    forever because the recovery sweep never re-syncs a terminal PARTIAL."""
    started = _sm().apply(_new(), FulfilmentEvent.START, now=NOW)
    out = _sm().apply(
        started,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=10),  # well within the grace period
        fulfilment=_k8s_transient_partial(),
    )
    assert out.status == RequestStatus.PARTIAL_PENDING
    assert not out.status.is_terminal()


def test_partial_with_failed_count_but_not_final_is_partial_pending():
    """Guard: finality is gated on ``final``, NOT on ``failed_count``.  A partial
    carrying failed_count > 0 but final=False (an async provider that saw a
    transient pod failure) must still hold in PARTIAL_PENDING so a later poll can
    heal it.  Proves we did not regress to a failed_count-based finality gate."""
    started = _sm().apply(_new(), FulfilmentEvent.START, now=NOW)
    out = _sm().apply(
        started,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=10),
        fulfilment=ProviderFulfilment(
            state="partial",  # type: ignore[arg-type]
            message="2/3",
            running_count=2,
            pending_count=0,
            failed_count=1,
            final=False,
        ),
    )
    assert out.status == RequestStatus.PARTIAL_PENDING
    assert not out.status.is_terminal()


def test_nonfinal_partial_within_deadline_is_partial_pending():
    """Guard: a still-gathering partial (final=False) within the deadline still
    uses the non-terminal PARTIAL_PENDING holding state."""
    started = _sm().apply(_new(), FulfilmentEvent.START, now=NOW)
    out = _sm().apply(
        started,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=10),
        fulfilment=_nonfinal_partial(),
    )
    assert out.status == RequestStatus.PARTIAL_PENDING
    assert not out.status.is_terminal()


def test_partial_without_final_flag_falls_back_to_deadline_rule():
    """When the provider does not declare finality (final defaults False),
    fall back to the deadline-based holding rule (PARTIAL_PENDING within the
    deadline)."""
    started = _sm().apply(_new(), FulfilmentEvent.START, now=NOW)
    out = _sm().apply(
        started,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=10),
        fulfilment=_verdict("partial"),  # final defaults to False
    )
    assert out.status == RequestStatus.PARTIAL_PENDING


# ---------------------------------------------------------------------------
# Stale shortfall diagnostic is cleared on a clean COMPLETED
# ---------------------------------------------------------------------------


def test_partial_pending_to_completed_clears_stale_diagnostic():
    """A PARTIAL_PENDING request carrying a CAPACITY 'Partially fulfilled 2/3'
    diagnostic that then receives a 'fulfilled' verdict must land on COMPLETED
    with the stale shortfall diagnostic cleared — a clean success has nothing to
    explain."""
    from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic

    start = _sm().apply(_new(), FulfilmentEvent.START, now=NOW)
    pending = _sm().apply(
        start,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=10),
        fulfilment=ProviderFulfilment(
            state="partial",  # type: ignore[arg-type]
            message="2/3",
            pending_count=1,
            diagnostic=FulfilmentDiagnostic(
                category=DiagnosticCategory.CAPACITY,
                summary="Partially fulfilled 2/3",
                occurred_at=NOW,
            ),
        ),
    )
    assert pending.status == RequestStatus.PARTIAL_PENDING
    assert pending.fulfilment_diagnostic is not None

    completed = _sm().apply(
        pending,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW + timedelta(seconds=20),
        fulfilment=_verdict("fulfilled"),
    )
    assert completed.status == RequestStatus.COMPLETED
    assert completed.fulfilment_diagnostic is None


def test_deadline_sweep_completes_clears_prior_diagnostic():
    """A deadline sweep that resolves to COMPLETED (full capacity via machine_ids,
    just no 'fulfilled' verdict in time) must clear any prior shortfall
    diagnostic seeded on the row."""
    from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic

    r = _new(
        RequestStatus.PARTIAL_PENDING,
        deadline_at=NOW,
        started_at=NOW - timedelta(seconds=GRACE),
        partial_since=NOW - timedelta(seconds=GRACE),
        requested_count=3,
        successful_count=0,
        machine_ids=["i-1", "i-2", "i-3"],
        fulfilment_diagnostic=FulfilmentDiagnostic(
            category=DiagnosticCategory.CAPACITY,
            summary="Partially fulfilled 2/3",
            occurred_at=NOW - timedelta(seconds=GRACE),
        ),
    )
    out = _sm().evaluate_deadline(r, now=NOW + timedelta(seconds=1))
    assert out.status == RequestStatus.COMPLETED
    assert out.fulfilment_diagnostic is None

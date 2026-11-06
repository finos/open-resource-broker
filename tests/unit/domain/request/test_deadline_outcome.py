"""Deadline outcome: what counts as fulfilled, when it is decided, and when release is owed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import FulfilmentEvent, FulfilmentStateMachine
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
GRACE = 300
STAMP = "deadline_release"


def _machine() -> FulfilmentStateMachine:
    return FulfilmentStateMachine(grace_period_seconds=GRACE)


def _expired(
    *,
    requested: int = 3,
    machine_ids: Optional[list[str]] = None,
    past_deadline: float = 10,
    snapshot: Optional[dict[str, Any]] = None,
    status: RequestStatus = RequestStatus.ACQUIRING,
) -> Request:
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl",
        machine_count=requested,
        provider_type="aws",
        provider_name="aws-default",
    )
    metadata = dict(request.metadata)
    if snapshot is not None:
        metadata["last_fulfilment"] = snapshot
    return request.model_copy(
        update={
            "status": status,
            "resource_ids": ["fleet-1"],
            "machine_ids": machine_ids if machine_ids is not None else [],
            "deadline_at": NOW - timedelta(seconds=past_deadline),
            "metadata": metadata,
        }
    )


def _ids(n: int) -> list[str]:
    return [f"i-{k}" for k in range(n)]


def test_ids_equal_to_requested_but_none_running_times_out_and_owes_release():
    request = _expired(machine_ids=_ids(3))

    result = _machine().evaluate_deadline(request, now=NOW, running_count=0, fresh_read=True)

    assert result.status == RequestStatus.TIMEOUT
    assert result.metadata[STAMP] == "pending"


def test_some_running_at_deadline_is_partial_and_owes_release():
    request = _expired(machine_ids=_ids(3))

    result = _machine().evaluate_deadline(request, now=NOW, running_count=2, fresh_read=True)

    assert result.status == RequestStatus.PARTIAL
    assert result.metadata[STAMP] == "pending"


def test_all_running_at_deadline_completes_without_release():
    request = _expired(machine_ids=_ids(3))

    result = _machine().evaluate_deadline(request, now=NOW, running_count=3, fresh_read=True)

    assert result.status == RequestStatus.COMPLETED
    assert STAMP not in result.metadata


def test_timeout_without_machine_ids_owes_nothing():
    request = _expired(machine_ids=[])

    result = _machine().evaluate_deadline(request, now=NOW, running_count=0, fresh_read=True)

    assert result.status == RequestStatus.TIMEOUT
    assert STAMP not in result.metadata


def test_fresh_read_with_unknown_count_keeps_the_ids_based_rule():
    request = _expired(machine_ids=_ids(3))

    result = _machine().evaluate_deadline(request, now=NOW, running_count=None, fresh_read=True)

    assert result.status == RequestStatus.COMPLETED
    assert STAMP not in result.metadata


def test_without_a_fresh_read_ids_defer_the_outcome_until_the_backstop():
    request = _expired(machine_ids=_ids(3), past_deadline=60)

    assert _machine().evaluate_deadline(request, now=NOW) is request


def test_without_machine_ids_the_outcome_is_not_deferred():
    request = _expired(machine_ids=[])

    result = _machine().evaluate_deadline(request, now=NOW)

    assert result.status == RequestStatus.TIMEOUT


def test_backstop_uses_the_stored_count_and_owes_release():
    request = _expired(
        machine_ids=_ids(3),
        past_deadline=GRACE + 601,
        snapshot={"state": "in_progress", "running_count": 1},
    )

    result = _machine().evaluate_deadline(request, now=NOW)

    assert result.status == RequestStatus.PARTIAL
    assert result.metadata[STAMP] == "pending"


def test_backstop_with_stored_zero_and_ids_is_partial_not_timeout():
    request = _expired(
        machine_ids=_ids(3),
        past_deadline=GRACE + 601,
        snapshot={"state": "in_progress", "running_count": 0},
    )

    result = _machine().evaluate_deadline(request, now=NOW)

    assert result.status == RequestStatus.PARTIAL
    assert result.metadata[STAMP] == "pending"


def test_backstop_with_snapshot_that_has_no_count_keeps_the_ids_based_rule():
    request = _expired(
        machine_ids=_ids(3),
        past_deadline=GRACE + 601,
        snapshot={"state": "in_progress", "running_count": None},
    )

    result = _machine().evaluate_deadline(request, now=NOW)

    assert result.status == RequestStatus.COMPLETED
    assert STAMP not in result.metadata


def test_backstop_without_a_snapshot_keeps_the_ids_based_rule():
    request = _expired(machine_ids=_ids(3), past_deadline=GRACE + 601)

    result = _machine().evaluate_deadline(request, now=NOW)

    assert result.status == RequestStatus.COMPLETED


def test_apply_passes_the_fresh_count_to_the_deadline_rule():
    request = _expired(machine_ids=_ids(3))
    verdict = ProviderFulfilment(state="in_progress", message="waiting")

    result = _machine().apply(
        request,
        FulfilmentEvent.PROVIDER_VERDICT,
        now=NOW,
        fulfilment=verdict,
        running_count=0,
        fresh_read=True,
    )

    assert result.status == RequestStatus.TIMEOUT
    assert result.metadata[STAMP] == "pending"


def test_a_pending_request_is_still_judged_before_the_deadline_guard():
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl",
        machine_count=2,
        provider_type="aws",
        provider_name="aws-default",
    ).model_copy(update={"created_at": NOW - timedelta(hours=3)})

    result = _machine().evaluate_deadline(request, now=NOW, fresh_read=True)

    assert result.status == RequestStatus.FAILED

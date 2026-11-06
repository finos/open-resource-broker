"""A request stuck in PENDING with nothing recorded is failed once it cannot still be live."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from orb.domain.base.diagnostic import DiagnosticCategory
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfillment_config import (
    fulfillment_defaults_from_request_config,
    merged_fulfillment_config,
)
from orb.domain.request.fulfilment_state_machine import FulfilmentEvent, FulfilmentStateMachine
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _pending(
    age_seconds: float,
    *,
    fulfillment_config: Any = None,
    request_type: RequestType = RequestType.ACQUIRE,
    resource_ids: list[str] | None = None,
) -> Request:
    request = Request.create_new_request(
        request_type=request_type,
        template_id="tpl",
        machine_count=2,
        provider_type="aws",
        provider_name="aws-default",
    )
    request = request.model_copy(update={"created_at": NOW - timedelta(seconds=age_seconds)})
    if fulfillment_config is not None:
        request = request.model_copy(
            update={"metadata": {**request.metadata, "fulfillment_config": fulfillment_config}}
        )
    if resource_ids:
        request = request.model_copy(update={"resource_ids": resource_ids})
    return request


def _machine(**kwargs: Any) -> FulfilmentStateMachine:
    return FulfilmentStateMachine(grace_period_seconds=300, **kwargs)


def test_old_pending_request_without_resources_is_failed_as_abandoned():
    request = _pending(age_seconds=3600)

    result = _machine().evaluate_deadline(request, now=NOW)

    assert result.status == RequestStatus.FAILED
    assert result.status_message == "Request abandoned before provisioning started"
    assert result.fulfilment_diagnostic is not None
    assert result.fulfilment_diagnostic.category == DiagnosticCategory.INTERNAL
    assert any(e.event_type == "RequestStatusChangedEvent" for e in result.get_domain_events())


def test_pending_request_younger_than_the_create_bound_is_untouched():
    # Past the grace period (300 s) but within timeout + dispatch timeout + margin (900 s).
    request = _pending(age_seconds=400)

    assert _machine().evaluate_deadline(request, now=NOW) is request


def test_pending_request_with_resource_ids_is_untouched():
    request = _pending(age_seconds=3600, resource_ids=["fleet-1"])

    assert _machine().evaluate_deadline(request, now=NOW) is request


def test_pending_return_request_is_untouched():
    request = _pending(age_seconds=3600, request_type=RequestType.RETURN)

    assert _machine().evaluate_deadline(request, now=NOW) is request


def test_bound_tracks_the_configured_timeouts():
    machine = _machine(
        fulfilment_defaults={"timeout_seconds": 1000, "dispatch_timeout_seconds": 500}
    )
    # 1000 + 500 + 300 = 1800

    assert machine.evaluate_deadline(_pending(1700), now=NOW).status == RequestStatus.PENDING
    assert machine.evaluate_deadline(_pending(1900), now=NOW).status == RequestStatus.FAILED


def test_built_in_defaults_apply_when_none_are_supplied():
    machine = FulfilmentStateMachine(grace_period_seconds=60)

    assert machine.abandon_after(_pending(0)) == timedelta(seconds=900)


def test_bound_is_never_shorter_than_the_grace_period():
    machine = FulfilmentStateMachine(grace_period_seconds=3600)

    assert machine.abandon_after(_pending(0)) == timedelta(seconds=3600)


def test_per_request_override_widens_the_bound():
    request_config = {"timeout_seconds": 1200}
    # 1200 + 300 (dispatch default) + 300 = 1800

    young = _pending(1000, fulfillment_config=request_config)
    old = _pending(1900, fulfillment_config=request_config)

    assert _machine().evaluate_deadline(young, now=NOW).status == RequestStatus.PENDING
    assert _machine().evaluate_deadline(old, now=NOW).status == RequestStatus.FAILED


@pytest.mark.parametrize(
    "override",
    ["not-a-dict", ["a"], 7, {"timeout_seconds": "abc"}, {"dispatch_timeout_seconds": None}],
)
def test_unusable_override_falls_back_to_defaults_without_raising(override):
    machine = _machine()

    assert machine.evaluate_deadline(
        _pending(400, fulfillment_config=override), now=NOW
    ).status == (RequestStatus.PENDING)
    assert machine.evaluate_deadline(
        _pending(3600, fulfillment_config=override), now=NOW
    ).status == (RequestStatus.FAILED)


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 1e300, -50])
def test_extreme_override_values_do_not_raise(value):
    request = _pending(3600, fulfillment_config={"timeout_seconds": value})

    result = _machine().evaluate_deadline(request, now=NOW)

    assert result.status in (RequestStatus.PENDING, RequestStatus.FAILED)


def test_other_events_on_an_abandoned_request_resolve_to_the_abandonment():
    result = _machine().apply(_pending(3600), FulfilmentEvent.DEADLINE_SWEEP, now=NOW)

    assert result.status == RequestStatus.FAILED


def test_cancel_still_wins_over_abandonment():
    result = _machine().apply(_pending(3600), FulfilmentEvent.CANCEL, now=NOW, reason="stop")

    assert result.status == RequestStatus.CANCELLED


class TestMergedFulfillmentConfig:
    """The merge is the shallow overlay provisioning has always used."""

    DEFAULTS = {
        "max_retries": 3,
        "timeout_seconds": 300,
        "dispatch_timeout_seconds": 300,
        "batch_size": 1000,
        "fallback_template_id": None,
    }

    def test_request_keys_replace_defaults_and_others_remain(self):
        request = _pending(0, fulfillment_config={"timeout_seconds": 60, "extra": 1})

        assert merged_fulfillment_config(self.DEFAULTS, request) == {
            **self.DEFAULTS,
            "timeout_seconds": 60,
            "extra": 1,
        }

    def test_no_override_returns_the_defaults(self):
        assert merged_fulfillment_config(self.DEFAULTS, _pending(0)) == self.DEFAULTS

    def test_defaults_are_not_mutated(self):
        defaults = dict(self.DEFAULTS)
        merged_fulfillment_config(defaults, _pending(0, fulfillment_config={"max_retries": 0}))

        assert defaults == self.DEFAULTS

    def test_defaults_come_from_the_request_config_keys(self):
        assert fulfillment_defaults_from_request_config({}) == self.DEFAULTS
        assert fulfillment_defaults_from_request_config(
            {
                "fulfillment_max_retries": 1,
                "fulfillment_timeout_seconds": 2,
                "dispatch_timeout_seconds": 3,
                "fulfillment_batch_size": 4,
                "fulfillment_fallback_template_id": "t",
            }
        ) == {
            "max_retries": 1,
            "timeout_seconds": 2,
            "dispatch_timeout_seconds": 3,
            "batch_size": 4,
            "fallback_template_id": "t",
        }

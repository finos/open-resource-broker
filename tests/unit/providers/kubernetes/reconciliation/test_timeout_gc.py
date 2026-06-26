"""Unit tests for the pod-timeout GC helpers.

Covers the two pure helpers exposed by
``orb.providers.kubernetes.reconciliation.timeout_gc``:

* :func:`is_pod_timed_out`     — predicate on a single instance dict;
* :func:`apply_pod_timeout`    — list transform that rewrites timed-out
  entries with ``status="terminated"`` and the
  ``provider_data.unschedulable_reason`` field populated from the
  original ``status_reason``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from orb.providers.kubernetes.reconciliation.timeout_gc import (
    apply_pod_timeout,
    is_pod_timed_out,
)


def _instance(
    *,
    status: str = "pending",
    launch_time: str | None = "2026-06-19T12:00:00Z",
    status_reason: str | None = None,
    provider_data: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "instance_id": "pod-a",
        "resource_id": "pod-a",
        "name": "pod-a",
        "status": status,
        "status_reason": status_reason,
        "launch_time": launch_time,
        "provider_data": provider_data or {"namespace": "orb"},
    }


# ---------------------------------------------------------------------------
# is_pod_timed_out
# ---------------------------------------------------------------------------


def test_pending_pod_past_timeout_is_timed_out() -> None:
    now = datetime(2026, 6, 19, 12, 10, 0, tzinfo=timezone.utc).timestamp()
    instance = _instance(status="pending", launch_time="2026-06-19T12:00:00Z")

    assert is_pod_timed_out(instance, pod_timeout_seconds=300, now=now) is True


def test_pending_pod_within_timeout_is_not_timed_out() -> None:
    now = datetime(2026, 6, 19, 12, 4, 0, tzinfo=timezone.utc).timestamp()
    instance = _instance(status="pending", launch_time="2026-06-19T12:00:00Z")

    assert is_pod_timed_out(instance, pod_timeout_seconds=300, now=now) is False


def test_running_pod_is_never_timed_out() -> None:
    now = datetime(2026, 6, 19, 12, 30, 0, tzinfo=timezone.utc).timestamp()
    instance = _instance(status="running", launch_time="2026-06-19T12:00:00Z")

    assert is_pod_timed_out(instance, pod_timeout_seconds=60, now=now) is False


def test_starting_pod_is_subject_to_timeout() -> None:
    now = datetime(2026, 6, 19, 12, 10, 0, tzinfo=timezone.utc).timestamp()
    instance = _instance(status="starting", launch_time="2026-06-19T12:00:00Z")

    assert is_pod_timed_out(instance, pod_timeout_seconds=120, now=now) is True


def test_missing_launch_time_is_not_treated_as_timed_out() -> None:
    now = datetime(2026, 6, 19, 12, 10, 0, tzinfo=timezone.utc).timestamp()
    instance = _instance(launch_time=None)

    assert is_pod_timed_out(instance, pod_timeout_seconds=60, now=now) is False


def test_malformed_launch_time_is_not_treated_as_timed_out() -> None:
    now = datetime(2026, 6, 19, 12, 10, 0, tzinfo=timezone.utc).timestamp()
    instance = _instance(launch_time="not-a-timestamp")

    assert is_pod_timed_out(instance, pod_timeout_seconds=60, now=now) is False


def test_naive_iso_timestamp_is_assumed_utc() -> None:
    """``datetime.fromisoformat`` accepts naive strings — we treat them as UTC."""
    now = datetime(2026, 6, 19, 12, 10, 0, tzinfo=timezone.utc).timestamp()
    instance = _instance(launch_time="2026-06-19 12:00:00")

    assert is_pod_timed_out(instance, pod_timeout_seconds=60, now=now) is True


# ---------------------------------------------------------------------------
# apply_pod_timeout
# ---------------------------------------------------------------------------


def test_apply_pod_timeout_rewrites_status_to_terminated() -> None:
    now = datetime(2026, 6, 19, 12, 10, 0, tzinfo=timezone.utc).timestamp()
    inputs = [
        _instance(status="pending", status_reason="Unschedulable"),
        _instance(status="running"),  # untouched
    ]

    result = apply_pod_timeout(inputs, pod_timeout_seconds=60, now=now)

    assert result[0]["status"] == "terminated"
    assert result[0]["status_reason"] == "Unschedulable"
    provider_data = result[0]["provider_data"]
    assert isinstance(provider_data, dict)
    assert provider_data["unschedulable_reason"] == "Unschedulable"
    assert provider_data["timed_out"] is True
    # Other instance is identity-preserving — same dict reference.
    assert result[1] is inputs[1]


def test_apply_pod_timeout_falls_back_to_default_reason() -> None:
    """No condition reason on the input -> the default ``Unschedulable`` is used."""
    now = datetime(2026, 6, 19, 12, 10, 0, tzinfo=timezone.utc).timestamp()
    inputs = [_instance(status="pending", status_reason=None)]

    result = apply_pod_timeout(inputs, pod_timeout_seconds=60, now=now)

    assert result[0]["status"] == "terminated"
    assert result[0]["status_reason"] == "Unschedulable"
    provider_data = result[0]["provider_data"]
    assert isinstance(provider_data, dict)
    assert provider_data["unschedulable_reason"] == "Unschedulable"


def test_apply_pod_timeout_does_not_mutate_input_list() -> None:
    now = datetime(2026, 6, 19, 12, 10, 0, tzinfo=timezone.utc).timestamp()
    inputs = [_instance(status="pending", status_reason="Unschedulable")]

    result = apply_pod_timeout(inputs, pod_timeout_seconds=60, now=now)

    # Input dict must NOT have been rewritten in place.
    assert inputs[0]["status"] == "pending"
    # Output dict is a different object.
    assert result[0] is not inputs[0]


def test_apply_pod_timeout_no_op_when_timeout_is_zero() -> None:
    now = datetime(2026, 6, 19, 12, 10, 0, tzinfo=timezone.utc).timestamp()
    inputs = [_instance(status="pending")]

    result = apply_pod_timeout(inputs, pod_timeout_seconds=0, now=now)

    # Zero / negative timeout disables the GC entirely.
    assert result == inputs
    assert result[0]["status"] == "pending"


def test_apply_pod_timeout_empty_list_returns_empty_list() -> None:
    assert apply_pod_timeout([], pod_timeout_seconds=60) == []


def test_apply_pod_timeout_preserves_unschedulable_reason_alongside_other_provider_data() -> None:
    now = datetime(2026, 6, 19, 12, 10, 0, tzinfo=timezone.utc).timestamp()
    inputs = [
        _instance(
            status="pending",
            status_reason="ImagePullBackOff",
            provider_data={
                "namespace": "orb",
                "node_name": "node-1",
                "phase": "Pending",
                "ready": False,
            },
        )
    ]

    result = apply_pod_timeout(inputs, pod_timeout_seconds=60, now=now)

    provider_data = result[0]["provider_data"]
    assert isinstance(provider_data, dict)
    assert provider_data["unschedulable_reason"] == "ImagePullBackOff"
    # Other fields untouched.
    assert provider_data["namespace"] == "orb"
    assert provider_data["node_name"] == "node-1"
    assert provider_data["phase"] == "Pending"
    assert provider_data["ready"] is False

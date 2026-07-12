"""Regression tests for StatefulSetStatusResolver.compute_fulfilment.

Mirrors test_deployment_fulfilment.py for the StatefulSet variant.
Focus: the partial-cache crashloop guard added to address the
adversarial review finding that a settled ProgressDeadlineExceeded
scenario with a partial watcher-cache view could hang in 'in_progress'
indefinitely instead of reporting 'failed'.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from orb.providers.k8s.infrastructure.handlers.statefulset_status import (
    StatefulSetStatusResolver,
)


def _resolver() -> StatefulSetStatusResolver:
    return StatefulSetStatusResolver(MagicMock())


def _inst(status: str) -> dict:
    return {"status": status}


# ---------------------------------------------------------------------------
# Baseline: existing branch behaviour (must not regress)
# ---------------------------------------------------------------------------


def test_mixed_failed_and_pending_stays_in_progress() -> None:
    """failed + still-pending replacement → in_progress, matching AWS parity."""
    resolver = _resolver()
    instances = [_inst("failed"), _inst("failed"), _inst("pending")]
    result = resolver.compute_fulfilment(instances, requested_count=3)
    assert result.state == "in_progress"
    assert result.failed_count == 2
    assert result.pending_count == 1


def test_all_failed_is_failed() -> None:
    """All-failed branch fires when every instance is failed."""
    resolver = _resolver()
    instances = [_inst("failed"), _inst("failed")]
    result = resolver.compute_fulfilment(instances, requested_count=2)
    assert result.state == "failed"


def test_all_ready_is_fulfilled() -> None:
    """All ready → fulfilled."""
    resolver = _resolver()
    instances = [_inst("running"), _inst("running")]
    result = resolver.compute_fulfilment(
        instances, requested_count=2, controller_view={"ready_replicas": 2}
    )
    assert result.state == "fulfilled"


# ---------------------------------------------------------------------------
# Regression: partial-cache crashloop guard (Fix 1)
# ---------------------------------------------------------------------------


def test_partial_cache_all_failed_no_pending_is_failed() -> None:
    """Partial-cache view: failed > 0, pending == 0, ready == 0 → failed.

    A settled crashloop where the watcher only returned a subset of pods.
    All visible pods are failed, nothing is pending or ready.  Must return
    'failed' (AWS fleet_fulfilment parity), not hang in 'in_progress'.
    """
    resolver = _resolver()
    # Only 2 of 5 pods visible in partial cache — both failed, none pending.
    instances = [_inst("failed"), _inst("failed")]
    result = resolver.compute_fulfilment(instances, requested_count=5)
    assert result.state == "failed", (
        "Partial-cache view with all-failed / no-pending pods must report 'failed', "
        "not hang in 'in_progress'"
    )
    assert result.failed_count == 2
    assert result.pending_count == 0


def test_partial_cache_failed_with_pending_stays_in_progress() -> None:
    """Partial-cache view: failed > 0, pending > 0 → in_progress (false-positive avoided).

    The controller may still be respawning pods.  Must NOT declare failed
    while replacement pods are still pending.
    """
    resolver = _resolver()
    instances = [_inst("failed"), _inst("pending")]
    result = resolver.compute_fulfilment(instances, requested_count=5)
    assert result.state == "in_progress", (
        "Partial-cache view with pending pods must stay 'in_progress', not be false-failed"
    )
    assert result.failed_count == 1
    assert result.pending_count == 1


def test_partial_cache_failed_with_ready_not_failed() -> None:
    """Partial-cache view: failed > 0, ready > 0 → partial, not failed."""
    resolver = _resolver()
    instances = [_inst("failed"), _inst("running")]
    result = resolver.compute_fulfilment(
        instances, requested_count=5, controller_view={"ready_replicas": 1}
    )
    assert result.state != "failed"


__all__: list[str] = []

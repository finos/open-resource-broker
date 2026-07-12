"""Tests for DeploymentStatusResolver.compute_fulfilment failure detection.

Focus: a crash-looping Deployment whose controller keeps respawning pods
leaves the instance list as a mix of failed + pending.  The resolver must
report ``failed`` rather than masking it as perpetual ``in_progress``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from orb.providers.k8s.infrastructure.handlers.deployment_status import (
    DeploymentStatusResolver,
)


def _resolver() -> DeploymentStatusResolver:
    return DeploymentStatusResolver(MagicMock())


def _inst(status: str) -> dict:
    return {"status": status}


def test_mixed_failed_and_pending_stays_in_progress() -> None:
    """failed + still-pending replacement → in_progress, matching AWS.

    A single transient pod failure during scale-up must NOT permanently fail
    the request while replacement pods are still pending — the controller may
    recover.  AWS (fleet_fulfilment) only declares failed when pending_count is
    also 0.  The request is bounded by the acquire timeout, not condemned early.
    """
    resolver = _resolver()
    instances = [_inst("failed"), _inst("failed"), _inst("pending")]
    result = resolver.compute_fulfilment(instances, requested_count=3)
    assert result.state == "in_progress"
    assert result.failed_count == 2
    assert result.pending_count == 1


def test_all_pending_no_failures_still_in_progress() -> None:
    """No failures yet — genuine scale-up stays in_progress."""
    resolver = _resolver()
    instances = [_inst("pending"), _inst("pending")]
    result = resolver.compute_fulfilment(instances, requested_count=2)
    assert result.state == "in_progress"


def test_some_ready_with_failure_is_not_failed() -> None:
    """At least one replica ready → not the not-progressing branch."""
    resolver = _resolver()
    instances = [_inst("running"), _inst("failed"), _inst("pending")]
    result = resolver.compute_fulfilment(
        instances, requested_count=3, controller_view={"ready_replicas": 1}
    )
    assert result.state != "failed"


def test_all_failed_is_failed() -> None:
    """The pre-existing all-failed branch still fires."""
    resolver = _resolver()
    instances = [_inst("failed"), _inst("failed")]
    result = resolver.compute_fulfilment(instances, requested_count=2)
    assert result.state == "failed"


# ---------------------------------------------------------------------------
# Regression: partial-cache crashloop guard (Fix 1)
# ---------------------------------------------------------------------------


def test_partial_cache_all_failed_no_pending_is_failed() -> None:
    """Partial-cache view: failed > 0, pending == 0, ready == 0 → failed.

    A settled ProgressDeadlineExceeded crashloop where the watcher's cache
    only returned a subset of pods (failed < len(instances) globally).  All
    visible pods are failed, nothing is pending or ready.  Must return 'failed'
    to match AWS fleet_fulfilment parity — not 'in_progress'.

    This is the true-positive case for the partial-cache guard.
    """
    resolver = _resolver()
    # Only 2 of 5 pods visible in partial cache — both failed, none pending.
    instances = [_inst("failed"), _inst("failed")]
    result = resolver.compute_fulfilment(instances, requested_count=5)
    assert result.state == "failed", (
        "A partial-cache view with all-failed / no-pending pods must report 'failed', "
        "not hang in 'in_progress'"
    )
    assert result.failed_count == 2
    assert result.pending_count == 0


def test_partial_cache_failed_with_pending_stays_in_progress() -> None:
    """Partial-cache view: failed > 0, pending > 0 → in_progress (false-positive avoided).

    The controller may still be respawning pods.  We must NOT declare failed
    while replacement pods are pending.  This is the guard that the earlier
    (incorrect) removal was right to target — the pending_count == 0 condition
    in the new branch prevents this false-positive.
    """
    resolver = _resolver()
    instances = [_inst("failed"), _inst("pending")]
    result = resolver.compute_fulfilment(instances, requested_count=5)
    assert result.state == "in_progress", (
        "A partial-cache view with pending pods must stay 'in_progress', not be false-failed"
    )
    assert result.failed_count == 1
    assert result.pending_count == 1

"""Unit tests for the fatal-waiting-reason classification.

Coverage:

* ``CrashLoopBackOff`` — added in the k8s hardening pass — is now
  correctly classified as fatal so a crash-looping pod escalates from
  ``pending`` / ``starting`` to ``failed`` rather than counting as
  live capacity indefinitely.
* The pre-existing fatal reasons remain in the frozenset.
* ``is_crash_loop_or_repeated_failure`` detects crash-loop behaviour
  even during the brief Running window between restarts.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from orb.providers.k8s.utilities.pod_state import (
    FATAL_WAITING_REASONS,
    is_crash_loop_or_repeated_failure,
    is_fatal_waiting_reason,
)

# ---------------------------------------------------------------------------
# Helpers — lightweight container-status stubs
# ---------------------------------------------------------------------------


def _cs(
    *,
    restart_count: int = 0,
    waiting_reason: str | None = None,
    last_terminated_exit_code: int | None = None,
) -> Any:
    """Build a minimal container-status stub for testing."""
    waiting = SimpleNamespace(reason=waiting_reason) if waiting_reason is not None else None
    state = SimpleNamespace(waiting=waiting, terminated=None)
    last_terminated = (
        SimpleNamespace(exit_code=last_terminated_exit_code)
        if last_terminated_exit_code is not None
        else None
    )
    last_state = SimpleNamespace(terminated=last_terminated)
    return SimpleNamespace(restart_count=restart_count, state=state, last_state=last_state)


def test_crashloopbackoff_is_fatal() -> None:
    assert "CrashLoopBackOff" in FATAL_WAITING_REASONS
    assert is_fatal_waiting_reason("CrashLoopBackOff") is True


def test_pre_existing_fatal_reasons_still_fatal() -> None:
    for reason in (
        "InvalidImageName",
        "ImagePullBackOff",
        "ErrImagePull",
        "CreateContainerConfigError",
        "CreateContainerError",
    ):
        assert reason in FATAL_WAITING_REASONS
        assert is_fatal_waiting_reason(reason) is True


def test_transient_reasons_are_not_fatal() -> None:
    for reason in ("ContainerCreating", "PodInitializing", None, ""):
        assert is_fatal_waiting_reason(reason) is False


# ---------------------------------------------------------------------------
# is_crash_loop_or_repeated_failure
# ---------------------------------------------------------------------------


class TestIsCrashLoopOrRepeatedFailure:
    """Validate the crash-loop / repeated-restart-failure detector."""

    def test_current_waiting_crashloopbackoff(self) -> None:
        """Current state is CrashLoopBackOff → always True."""
        cs = _cs(waiting_reason="CrashLoopBackOff", restart_count=1)
        assert is_crash_loop_or_repeated_failure([cs]) is True

    def test_running_with_high_restart_and_nonzero_last_exit(self) -> None:
        """Container is briefly Running but last terminated with exit != 0 and
        restart_count >= threshold — this is the masking window."""
        cs = _cs(restart_count=2, last_terminated_exit_code=1)
        assert is_crash_loop_or_repeated_failure([cs]) is True

    def test_running_with_high_restart_but_zero_last_exit(self) -> None:
        """exit_code=0 means successful completion — not a crash loop."""
        cs = _cs(restart_count=3, last_terminated_exit_code=0)
        assert is_crash_loop_or_repeated_failure([cs]) is False

    def test_single_restart_below_threshold(self) -> None:
        """restart_count=1 is below the default threshold of 2; not a crash loop yet."""
        cs = _cs(restart_count=1, last_terminated_exit_code=137)
        assert is_crash_loop_or_repeated_failure([cs]) is False

    def test_no_restarts_no_crash(self) -> None:
        """First run — not a crash loop."""
        cs = _cs(restart_count=0)
        assert is_crash_loop_or_repeated_failure([cs]) is False

    def test_empty_container_statuses(self) -> None:
        """Empty list → no crash."""
        assert is_crash_loop_or_repeated_failure([]) is False

    def test_custom_threshold(self) -> None:
        """Custom threshold=1: single restart with bad exit → crash loop."""
        cs = _cs(restart_count=1, last_terminated_exit_code=1)
        assert is_crash_loop_or_repeated_failure([cs], restart_threshold=1) is True

    def test_no_last_state_terminated(self) -> None:
        """High restart count but no last_state.terminated — can't confirm crash."""
        cs = _cs(restart_count=5)
        assert is_crash_loop_or_repeated_failure([cs]) is False

    def test_onfailure_skips_restart_count_heuristic(self) -> None:
        """A restartPolicy=OnFailure pod that retries is NOT condemned by restart count.

        Repeated restarts with a non-zero exit are the intended retry semantics
        for OnFailure, so the restart-count heuristic must not fire.
        """
        cs = _cs(restart_count=3, last_terminated_exit_code=1)
        assert is_crash_loop_or_repeated_failure([cs], restart_policy="OnFailure") is False

    def test_onfailure_still_fatal_on_crashloopbackoff(self) -> None:
        """OnFailure does not mask Kubernetes' own CrashLoopBackOff signal."""
        cs = _cs(waiting_reason="CrashLoopBackOff", restart_count=5)
        assert is_crash_loop_or_repeated_failure([cs], restart_policy="OnFailure") is True

    def test_always_still_uses_restart_count_heuristic(self) -> None:
        """Always/Never pods keep the restart-count crash-loop heuristic."""
        cs = _cs(restart_count=2, last_terminated_exit_code=1)
        assert is_crash_loop_or_repeated_failure([cs], restart_policy="Always") is True

"""Unit tests for the fatal-waiting-reason classification.

Coverage:

* ``CrashLoopBackOff`` — added in the k8s hardening pass — is now
  correctly classified as fatal so a crash-looping pod escalates from
  ``pending`` / ``starting`` to ``failed`` rather than counting as
  live capacity indefinitely.
* The pre-existing fatal reasons remain in the frozenset.
"""

from __future__ import annotations

from orb.providers.k8s.utilities.pod_state import (
    FATAL_WAITING_REASONS,
    is_fatal_waiting_reason,
)


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

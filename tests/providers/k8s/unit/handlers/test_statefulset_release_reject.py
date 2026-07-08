"""Unit tests for the StatefulSet handler's release-time victim guard.

The StatefulSet controller can only scale down from the top of the
ordinal stack; asking to remove a middle-ordinal pod would silently
evict a different pod.  ``_reject_non_highest_ordinal_victims`` refuses
the request instead so callers are surfaced the mismatch explicitly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.exceptions.k8s_errors import K8sError
from orb.providers.k8s.infrastructure.handlers.statefulset_handler import (
    K8sStatefulSetHandler,
)


def _make_handler() -> K8sStatefulSetHandler:
    """Build a handler with just enough scaffolding to invoke the guard.

    The guard is a pure function of its arguments — the handler's
    ``__init__`` dependencies (k8s client, config, cache) are only
    exercised by other methods, so a bare ``object.__new__`` instance
    with a mock logger is sufficient.
    """
    handler = object.__new__(K8sStatefulSetHandler)
    handler._logger = MagicMock()  # type: ignore[attr-defined]
    return handler


def test_reject_when_victims_are_middle_ordinals() -> None:
    handler = _make_handler()
    with pytest.raises(K8sError, match="StatefulSet selective release refused"):
        handler._reject_non_highest_ordinal_victims(
            statefulset_name="orb-abc",
            current_replicas=5,
            requested_victims=["orb-abc-1", "orb-abc-2"],
            request_id="req-test",
        )


def test_accept_when_victims_are_top_of_stack() -> None:
    handler = _make_handler()
    # Should not raise — top-of-stack for 5 replicas releasing 2 = pods 3,4.
    handler._reject_non_highest_ordinal_victims(
        statefulset_name="orb-abc",
        current_replicas=5,
        requested_victims=["orb-abc-3", "orb-abc-4"],
        request_id="req-test",
    )


def test_accept_when_no_victims() -> None:
    handler = _make_handler()
    handler._reject_non_highest_ordinal_victims(
        statefulset_name="orb-abc",
        current_replicas=5,
        requested_victims=[],
        request_id="req-test",
    )


def test_reject_when_partial_top_of_stack_partial_middle() -> None:
    handler = _make_handler()
    with pytest.raises(K8sError):
        handler._reject_non_highest_ordinal_victims(
            statefulset_name="orb-abc",
            current_replicas=5,
            requested_victims=["orb-abc-4", "orb-abc-2"],
            request_id="req-test",
        )

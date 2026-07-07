"""T12 — StatefulSet ordinal parse hot path: rsplit-based implementation.

Verifies:
1. Correctness parity with the previous startswith + isdigit implementation
   for all name shapes.
2. Handles non-numeric suffix (returns None, no exception).
3. Handles hyphenated statefulset names (rsplit at rightmost dash).
4. Performance: parses 1000 pod names without raising (timing guard).
"""

from __future__ import annotations

import time
from typing import Optional

import pytest

from orb.providers.k8s.utilities.statefulset_spec import parse_statefulset_pod_ordinal

# ---------------------------------------------------------------------------
# Reference implementation (old startswith path) used for equivalence check
# ---------------------------------------------------------------------------


def _old_parse(pod_name: str, statefulset_name: str) -> Optional[int]:
    """Exact copy of the pre-rsplit implementation for equivalence testing."""
    if not pod_name or not statefulset_name:
        return None
    prefix = f"{statefulset_name}-"
    if not pod_name.startswith(prefix):
        return None
    suffix = pod_name[len(prefix) :]
    if not suffix.isdigit():
        return None
    return int(suffix)


# ---------------------------------------------------------------------------
# Correctness tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pod_name,statefulset_name,expected",
    [
        # Standard ordinal patterns.
        ("orb-deadbeef-0", "orb-deadbeef", 0),
        ("orb-deadbeef-1", "orb-deadbeef", 1),
        ("orb-deadbeef-99", "orb-deadbeef", 99),
        ("orb-deadbeef-1000", "orb-deadbeef", 1000),
        # Wrong prefix.
        ("other-sts-0", "orb-deadbeef", None),
        # Statefulset name with internal hyphens — rsplit correctly
        # splits at the rightmost dash.
        ("my-complex-name-5", "my-complex-name", 5),
        ("my-complex-name-42", "my-complex-name", 42),
        # Non-numeric suffix.
        ("orb-deadbeef-abc", "orb-deadbeef", None),
        ("orb-deadbeef-1a", "orb-deadbeef", None),
        # Empty inputs.
        ("", "orb-deadbeef", None),
        ("orb-deadbeef-0", "", None),
        ("", "", None),
        # No dash in pod_name.
        ("orbdeadbeef0", "orb-deadbeef", None),
        # Suffix is empty string (trailing dash).
        ("orb-deadbeef-", "orb-deadbeef", None),
        # Negative-ordinal-shaped suffix must be rejected — a real
        # StatefulSet cannot have a negative ordinal, so accepting one
        # (as int("-1") would) risks poisoning scale-down sorts if a
        # rogue pod is manually created with a matching name.
        ("orb-deadbeef--1", "orb-deadbeef", None),
        # Leading-zero-shaped suffix must be rejected — a real
        # StatefulSet ordinal is written without leading zeros; parsing
        # "007" as 7 would collide with the legitimate ordinal 7.
        ("orb-deadbeef-007", "orb-deadbeef", None),
        # Single "0" is a legal ordinal (no leading zeros because there
        # is only one digit) — must remain accepted.
        ("orb-deadbeef-0", "orb-deadbeef", 0),
        # Whitespace-padded suffix must be rejected (isdigit rejects it too).
        ("orb-deadbeef- 1", "orb-deadbeef", None),
    ],
)
def test_parse_statefulset_pod_ordinal(
    pod_name: str, statefulset_name: str, expected: Optional[int]
) -> None:
    result = parse_statefulset_pod_ordinal(pod_name, statefulset_name)
    assert result == expected


# ---------------------------------------------------------------------------
# Equivalence with old implementation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pod_name,statefulset_name",
    [
        # Well-formed ordinals — must be equivalent.
        ("orb-abc-0", "orb-abc"),
        ("orb-abc-1", "orb-abc"),
        ("orb-abc-999", "orb-abc"),
        # Wrong prefix — both must return None.
        ("other-0", "orb-abc"),
        # Non-numeric suffix — both must return None.
        ("orb-abc-x", "orb-abc"),
        # Internal hyphens in the statefulset name — new impl uses
        # rsplit, must match old impl's startswith path.
        ("hyphen-name-sts-7", "hyphen-name-sts"),
        # Empty inputs — both must return None.
        ("", "orb-abc"),
        ("orb-abc-0", ""),
        # No hyphen at all — both must return None.
        ("orbabc0", "orb-abc"),
        # Trailing dash / empty suffix — both must return None.
        ("orb-abc-", "orb-abc"),
    ],
)
def test_parse_equivalence_with_old_implementation(pod_name: str, statefulset_name: str) -> None:
    """New rsplit implementation must produce identical output to the old
    startswith + isdigit implementation for the *legacy-accepted* input
    shapes.  Divergence cases (negative sign, leading zeros) are covered
    by dedicated correctness assertions above — this table only covers
    the shapes where both implementations agree by design."""
    new_result = parse_statefulset_pod_ordinal(pod_name, statefulset_name)
    old_result = _old_parse(pod_name, statefulset_name)
    assert new_result == old_result, (
        f"Diverged for pod_name={pod_name!r} statefulset_name={statefulset_name!r}: "
        f"new={new_result!r} old={old_result!r}"
    )


@pytest.mark.parametrize(
    "pod_name,statefulset_name,expected",
    [
        # Old impl accepted "-1" (int("-1") = -1); new impl rejects.
        # Assertion documents the intentional divergence.
        ("orb-abc--1", "orb-abc", None),
        # Old impl accepted "007" (int("007") = 7); new impl rejects.
        ("orb-abc-007", "orb-abc", None),
    ],
)
def test_parse_divergence_from_old_implementation(
    pod_name: str, statefulset_name: str, expected: Optional[int]
) -> None:
    """Cases where the new impl intentionally rejects inputs the old
    impl accepted (negative ordinals, leading zeros).  These are the
    tightened-validation cases the divergence is *supposed* to catch.
    """
    assert parse_statefulset_pod_ordinal(pod_name, statefulset_name) == expected


# ---------------------------------------------------------------------------
# T12 performance: parse 1000 pod names quickly
# ---------------------------------------------------------------------------


def test_parse_1000_pod_names_performance() -> None:
    """Parsing 1000 pod names must complete in under 0.1 seconds.

    The rsplit hot path has no regex compilation overhead and should handle
    this volume well within the budget on any CI machine.
    """
    sts_name = "orb-perf-test"
    names = [f"{sts_name}-{i}" for i in range(1000)]

    start = time.perf_counter()
    results = [parse_statefulset_pod_ordinal(n, sts_name) for n in names]
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1, f"1000 ordinal parses took {elapsed:.3f}s (budget: 0.1s)"
    assert results == list(range(1000))


def test_parse_1000_pod_names_equivalence() -> None:
    """rsplit output must match old implementation for all 1000 names."""
    sts_name = "orb-equivalence-sts"
    names = [f"{sts_name}-{i}" for i in range(1000)]

    for pod_name in names:
        new = parse_statefulset_pod_ordinal(pod_name, sts_name)
        old = _old_parse(pod_name, sts_name)
        assert new == old, f"Diverged at {pod_name!r}: new={new} old={old}"

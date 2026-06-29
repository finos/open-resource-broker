"""Test helpers for live AWS capacity-aware terminal-status assertions.

Background:
    The HostFactory wire contract treats ``complete_with_error`` as a
    legitimate terminal partial-success status — used today when a fleet
    or batch returned fewer instances than requested.  ORB stamps the
    domain ``PARTIAL`` status (which HF maps to ``complete_with_error``)
    whenever the provider settled with ``running_count < requested_count``.

    Real-AWS capacity is a moving target: an EC2Fleet INSTANT request for
    4 t3.medium ondemand instances may receive 4 today and 2 tomorrow,
    purely because of AZ-level supply.  The live test suite must
    differentiate "ORB correctly handled an AWS capacity shortfall" from
    "ORB has a bug stamping partial for an unrelated reason".

    Until the structured ``FulfilmentDiagnostic`` field is wired through
    (see beads epic open-resource-broker-2418, sub-task ...-2444), this
    helper falls back to a message-text heuristic.  The HF response's
    ``message`` already carries the shortfall in the form
    ``"Partially fulfilled: X/Y instances"`` or
    ``"Instant fleet: X/Y instance(s) running"``.

Contract:
    ``assert_terminal_ok(status_response, requested_count)`` accepts:
      - ``status == "complete"`` with ``fulfilled_units >= requested``
      - ``status == "complete_with_error"`` with ``fulfilled_units >= 1``
        AND a message string that names the shortfall (``"X/Y"`` pattern)

    Otherwise it raises ``pytest.fail``.

    The intent is: real capacity shortfalls — where AWS gave the provider
    some-but-not-all of the requested capacity — count as a passing test
    for ORB.  Zero-fulfilment or non-capacity-shaped errors still fail.
"""

from __future__ import annotations

import re
from typing import Any

import pytest


# Match the canonical "X/Y" partial-fulfilment pattern that ORB stamps on
# partial messages (e.g. "Partially fulfilled: 2/4 instances",
# "Instant fleet: 2/4 instance(s) running").
_PARTIAL_PATTERN = re.compile(r"\b\d+\s*/\s*\d+\b")


def assert_terminal_ok(status_response: dict[str, Any], requested_count: int) -> None:
    """Assert a terminal status response is a success or an AWS-capacity flake.

    Args:
        status_response: The full getRequestStatus response dict.
        requested_count: The capacity the caller asked for; used as the
            fallback ``target_units`` when the response does not surface
            one (e.g. older scheduler builds).

    Raises:
        pytest.fail (via ``pytest.fail``) when the response represents a
        genuine failure rather than an AWS capacity shortfall.
    """
    requests = status_response.get("requests") or []
    if not requests:
        pytest.fail(f"empty requests array in status_response: {status_response!r}")
    req = requests[0]
    status = req.get("status")
    if status is None:
        pytest.fail(f"missing status field in request: {req!r}")

    target_units = _coerce_int(req.get("target_units"), requested_count)
    fulfilled_units = _coerce_int(
        req.get("fulfilled_units"),
        len(req.get("machine_ids") or req.get("machines") or []),
    )
    message = (req.get("message") or "").strip()

    if status == "complete":
        if fulfilled_units < target_units:
            pytest.fail(
                f"status=complete but fulfilled_units={fulfilled_units} "
                f"< target_units={target_units}: {req!r}"
            )
        return

    if status in {"complete_with_error", "partial"}:
        if fulfilled_units < 1:
            pytest.fail(
                f"status={status!r} with zero fulfilled units is a bug "
                f"(no instances delivered, no capacity reported): {req!r}"
            )
        if not _PARTIAL_PATTERN.search(message):
            pytest.fail(
                f"status={status!r} but message does not name the shortfall "
                f"(missing 'X/Y' pattern): {message!r}"
            )
        return

    pytest.fail(f"unexpected terminal status {status!r}: {req!r}")


def _coerce_int(value: Any, fallback: int) -> int:
    """Coerce a possibly-missing/possibly-stringy value to int with fallback."""
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback

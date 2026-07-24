"""HostFactory wire-format capture-and-diff gate (Boundary A, value regression).

The schema tests in ``test_hf_contract.py`` prove every HF response has the
right *shape*.  This gate proves every emitted *value* is the one external
HostFactory integrations expect — it catches a silent status/result string
change that still passes ``additionalProperties`` + enum validation (e.g.
``"complete"`` drifting to ``"complete_with_error"``).

How the gate works
------------------
* ``capture_wire_format`` drives the real ``HostFactorySchedulerStrategy``
  formatter over the full domain-status x machine-status matrix and records the
  exact emitted wire strings.
* The committed baseline (``tests/fixtures/wire_format_baseline.json``) encodes
  ``origin/main``'s pre-redesign contract (see ``wire_format.py`` for why the
  branch capture is a faithful origin baseline).
* ``test_wire_format_matches_baseline`` fails loud on:
    - a changed value for any baseline key (accidental drift),
    - a removed baseline key,
    - a NEW key that is not in ``INTENTIONAL_ADDITIONS`` (undocumented state).
  It allows exactly the documented redesign additions.

To intentionally re-baseline after a *documented* wire-contract change, run the
suite with ``--capture-wire-format``; the baseline file is rewritten and the
diff assertions are skipped for that run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .wire_format import (
    INTENTIONAL_ADDITIONS,
    baseline_from_capture,
    capture_wire_format,
)

BASELINE_PATH = Path(__file__).parent.parent / "fixtures" / "wire_format_baseline.json"


def _load_baseline() -> dict[str, str]:
    if not BASELINE_PATH.exists():
        pytest.fail(
            f"Wire-format baseline missing at {BASELINE_PATH}. "
            "Re-generate it with: pytest tests/contract/test_wire_format_gate.py "
            "--capture-wire-format"
        )
    with open(BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_wire_format_capture_is_stable(hf_strategy):
    """Capturing twice yields identical output — the harness itself is deterministic."""
    first = capture_wire_format(hf_strategy)
    second = capture_wire_format(hf_strategy)
    assert first == second, "Wire-format capture is non-deterministic"
    assert first, "Wire-format capture produced no probes"


def test_wire_format_matches_baseline(request, hf_strategy):
    """The emitted HF wire values match the committed origin baseline.

    Fails loud on any value drift, any removed baseline key, and any new key
    outside the documented intentional-additions allowlist.
    """
    captured = capture_wire_format(hf_strategy)

    if request.config.getoption("--capture-wire-format"):
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        baseline = baseline_from_capture(captured)
        with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
            json.dump(baseline, fh, indent=2, sort_keys=True)
            fh.write("\n")
        pytest.skip(f"Re-captured wire-format baseline ({len(baseline)} probes) to {BASELINE_PATH}")

    baseline = _load_baseline()

    # 1. No baseline value silently changed and none was removed.
    drifted: list[str] = []
    removed: list[str] = []
    for key, expected in baseline.items():
        if key not in captured:
            removed.append(key)
        elif captured[key] != expected:
            drifted.append(f"  {key}: baseline={expected!r} -> emitted={captured[key]!r}")

    # 2. Any new key must be a documented intentional addition.
    unexpected_new: list[str] = []
    for key, value in captured.items():
        if key not in baseline and key not in INTENTIONAL_ADDITIONS:
            unexpected_new.append(f"  {key} -> {value!r}")

    failures: list[str] = []
    if drifted:
        failures.append(
            "HostFactory wire VALUE drift (breaks external integrations):\n" + "\n".join(drifted)
        )
    if removed:
        failures.append(
            "HostFactory wire keys REMOVED from the contract:\n  " + "\n  ".join(removed)
        )
    if unexpected_new:
        failures.append(
            "Undocumented NEW HostFactory wire states (add to INTENTIONAL_ADDITIONS "
            "in tests/contract/wire_format.py only if deliberate, then re-baseline):\n"
            + "\n".join(unexpected_new)
        )

    assert not failures, (
        "\n\n".join(failures)
        + "\n\nIf this change is intentional and documented, re-baseline with:\n"
        + "  pytest tests/contract/test_wire_format_gate.py --capture-wire-format"
    )


def test_intentional_additions_are_present_in_capture(hf_strategy):
    """Every documented intentional addition still exists in the live output.

    Guards against the allowlist rotting: if a redesign state is later removed,
    its stale entry in INTENTIONAL_ADDITIONS is flagged here rather than silently
    masking a future collision.
    """
    captured = capture_wire_format(hf_strategy)
    stale = sorted(INTENTIONAL_ADDITIONS - set(captured))
    assert not stale, (
        "INTENTIONAL_ADDITIONS lists states no longer emitted by the formatter "
        f"(remove them from tests/contract/wire_format.py): {stale}"
    )


# ---------------------------------------------------------------------------
# Pagination-leak gate: the HF getRequestStatus wire payload must NOT carry the
# LIST pagination fields (next_cursor/total_count). The IBM Symphony HF spec has
# no pagination cursor on a single request-status response.
# ---------------------------------------------------------------------------


def _request_status_dto(status: str = "complete"):
    from datetime import datetime, timezone

    from orb.application.request.dto import RequestDTO

    return RequestDTO(
        request_id="req-00000000-0000-0000-0000-000000000001",
        status=status,
        requested_count=1,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        machine_references=[],
        request_type="acquire",
    )


def test_hf_single_status_payload_has_no_pagination_fields(hf_strategy):
    """The real getRequestStatus production path — ResponseFormattingService
    wrapping the HostFactory strategy — must emit no pagination cursor.

    ResponseFormattingService.format_request_status is shared with the LIST
    endpoints; this locks in that the single/HF caller (no pagination kwargs)
    never leaks next_cursor/total_count into the HF wire payload.
    """
    from orb.interface.response_formatting_service import ResponseFormattingService

    formatter = ResponseFormattingService(hf_strategy)
    payload = formatter.format_request_status([_request_status_dto()]).data

    assert isinstance(payload, dict)
    assert "requests" in payload
    assert "next_cursor" not in payload, (
        "HostFactory getRequestStatus payload leaked a pagination cursor — "
        "the IBM Symphony HF spec has no next_cursor on a single request status"
    )
    assert "total_count" not in payload


def test_list_request_status_payload_keeps_pagination_fields(hf_strategy):
    """The LIST path (pagination kwargs supplied) STILL carries next_cursor and
    total_count so REST/UI load-more keeps working — even on a last page where
    next_cursor is None the key must be present."""
    from orb.interface.response_formatting_service import ResponseFormattingService

    formatter = ResponseFormattingService(hf_strategy)
    payload = formatter.format_request_status(
        [_request_status_dto()], total_count=1, next_cursor=None
    ).data

    assert isinstance(payload, dict)
    assert "next_cursor" in payload, "LIST response must keep next_cursor for UI pagination"
    assert payload["next_cursor"] is None
    assert payload["total_count"] == 1

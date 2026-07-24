"""Wire-format capture-and-diff harness for HostFactory response values.

Boundary A schema tests (``test_hf_contract.py``) enforce the *shape* of every
HostFactory response — required keys, enum membership, ``additionalProperties``.
They do NOT catch a silent change to an individual emitted VALUE: mapping the
domain ``"complete"`` status to ``"complete_with_error"`` instead of
``"complete"`` still validates against the schema (both are enum members) yet
would break every external HostFactory integration that branches on the exact
string.

This module captures the exact emitted HostFactory wire values — the request
``status`` string and the per-machine ``result`` string — by exercising the
real ``HostFactorySchedulerStrategy`` formatter over the full domain status and
machine-status matrix.  ``test_wire_format_gate.py`` diffs the captured values
against a committed baseline (``tests/fixtures/wire_format_baseline.json``) and
fails loud on any drift the schema tests would wave through.

Baseline strategy
-----------------
The committed baseline encodes ``origin/main``'s pre-redesign wire contract.
This is a *true* origin baseline even though it is captured on the redesign
branch, because the redesign changed no existing value:

* ``map_domain_status_to_hostfactory`` only *added* keys (``partial_pending``,
  and an explicit ``acquiring`` entry); every pre-existing domain status maps to
  the identical HF value it did on ``origin/main``.
* ``map_machine_status_to_result`` is byte-identical to ``origin/main``.

The single genuinely-new domain state introduced by the redesign
(``partial_pending``) is recorded in :data:`INTENTIONAL_ADDITIONS`.  The gate
verifies every origin-baseline value is unchanged, permits exactly the
intentional additions, and fails on any *other* new state, removed state, or
value drift.  So a future accidental value change (or an undocumented new
status) fails the gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

# Domain request-status strings probed against the HF ``status`` field.  Covers
# every RequestStatus enum value plus the ``error`` alias the mapper accepts.
# The order is stable so the captured JSON is deterministic.
PROBED_DOMAIN_STATUSES: tuple[str, ...] = (
    "pending",
    "in_progress",
    "acquiring",
    "provisioning",
    "partial_pending",
    "complete",
    "completed",
    "partial",
    "failed",
    "cancelled",
    "timeout",
    "error",
)

# Machine lifecycle statuses probed against the HF ``result`` field, across both
# request contexts (``acquire`` flips success/fail semantics vs ``return``).
PROBED_MACHINE_STATUSES: tuple[str, ...] = (
    "pending",
    "launching",
    "running",
    "shutting-down",
    "stopping",
    "stopped",
    "terminating",
    "terminated",
    "failed",
    "error",
    "unknown",
)

PROBED_REQUEST_TYPES: tuple[str, ...] = ("acquire", "return")

# Domain states deliberately introduced by the fulfilment redesign that are
# absent from ``origin/main``'s wire contract.  The gate permits these to appear
# in the current capture without a matching baseline entry; any OTHER new key
# fails.  Keep this list minimal and documented — it is the audit trail for
# every intentional wire-contract addition.
INTENTIONAL_ADDITIONS: frozenset[str] = frozenset(
    {
        # PARTIAL_PENDING: non-terminal holding state added by the fulfilment
        # state machine. Maps to "running" (still active) on the HF wire.
        "request_status:partial_pending",
    }
)


def _make_request_status_dto(
    status: str,
    machine_refs: list[Any] | None = None,
    request_type: str = "acquire",
) -> Any:
    from datetime import datetime, timezone

    from orb.application.request.dto import RequestDTO

    return RequestDTO(
        request_id="req-00000000-0000-0000-0000-000000000001",
        status=status,
        requested_count=1,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        machine_references=machine_refs or [],
        request_type=request_type,
    )


def _make_machine_ref_dto(status: str) -> Any:
    from orb.application.request.dto import MachineReferenceDTO

    return MachineReferenceDTO(
        machine_id="i-0abc1234def56789a",
        name="i-0abc1234def56789a",
        result="executing",
        status=status,
        private_ip_address="10.0.1.5",
        launch_time=0,
        message="",
    )


def capture_wire_format(hf_strategy: "HostFactorySchedulerStrategy") -> dict[str, str]:
    """Capture the emitted HostFactory wire values from the real formatter.

    Returns a flat ``{probe_key: emitted_value}`` map.  Probe keys are namespaced
    so request-status and machine-result probes never collide:

    * ``"request_status:<domain_status>"`` -> emitted ``requests[0].status``
    * ``"machine_result:<request_type>:<machine_status>"`` -> emitted
      ``requests[0].machines[0].result``

    Every value is produced by driving ``format_request_status_response`` — the
    exact code path that emits HF responses in production — so the capture
    reflects the real wire output, not an isolated mapper call.
    """
    captured: dict[str, str] = {}

    for domain_status in PROBED_DOMAIN_STATUSES:
        dto = _make_request_status_dto(domain_status)
        response = hf_strategy.format_request_status_response([dto])
        emitted = response["requests"][0]["status"]
        captured[f"request_status:{domain_status}"] = emitted

    for request_type in PROBED_REQUEST_TYPES:
        for machine_status in PROBED_MACHINE_STATUSES:
            machine = _make_machine_ref_dto(machine_status)
            dto = _make_request_status_dto(
                "in_progress", machine_refs=[machine], request_type=request_type
            )
            response = hf_strategy.format_request_status_response([dto])
            machines = response["requests"][0]["machines"]
            emitted = machines[0]["result"] if machines else "<no-machine>"
            captured[f"machine_result:{request_type}:{machine_status}"] = emitted

    return dict(sorted(captured.items()))


def baseline_from_capture(captured: dict[str, str]) -> dict[str, str]:
    """Filter a fresh capture down to ``origin/main``'s wire contract.

    Drops the intentional post-redesign additions so the committed baseline file
    encodes only the pre-redesign contract that must never silently change.
    """
    return {k: v for k, v in captured.items() if k not in INTENTIONAL_ADDITIONS}

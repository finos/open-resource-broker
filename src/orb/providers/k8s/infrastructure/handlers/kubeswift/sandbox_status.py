"""Map ``SwiftSandbox`` objects to ORB machine status."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic
from orb.domain.base.provider_fulfilment import FulfilmentState, ProviderFulfilment

GUEST_RUNNING = "GuestRunning"


def _condition(obj: dict[str, Any], condition_type: str) -> Optional[dict[str, Any]]:
    for condition in (obj.get("status") or {}).get("conditions") or []:
        if condition.get("type") == condition_type:
            return condition
    return None


def sandbox_machine_status(obj: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Return ``(ORB status, reason)`` for one ``SwiftSandbox``.

    ``running`` needs the guest itself up (``GuestRunning=True``), not only the
    ``Running`` phase: a launcher that started is not yet a usable machine.
    """
    status = obj.get("status") or {}
    phase = status.get("phase") or "Pending"
    message = status.get("message") or None

    if (obj.get("metadata") or {}).get("deletionTimestamp"):
        return "shutting-down", message
    if phase in ("Pending", "Materializing"):
        return "pending", message
    if phase == "Running":
        condition = _condition(obj, GUEST_RUNNING)
        if condition and condition.get("status") == "True":
            return "running", None
        return "launching", (condition or {}).get("message") or message
    if phase == "Exporting":
        return "shutting-down", message
    if phase == "Completed":
        return "terminated", message
    if phase == "Failed":
        return "failed", message
    return "unknown", f"unrecognised SwiftSandbox phase {phase!r}"


def reportable_ip(obj: dict[str, Any]) -> Optional[str]:
    """Return the guest address only when it is reachable outside its launcher.

    A ``Pod``-scope address is on the launcher pod's private NAT network and
    repeats across sandboxes, so it is never reported as ``private_ip``.
    """
    network = (obj.get("status") or {}).get("network") or {}
    if network.get("primaryIPScope") == "Network":
        return network.get("primaryIP") or None
    return None


def fulfilment_for(instances: list[dict[str, Any]], target: int) -> ProviderFulfilment:
    """Summarise per-sandbox status into the request's fulfilment verdict."""
    running = sum(1 for i in instances if i["status"] == "running")
    pending = sum(1 for i in instances if i["status"] in ("pending", "launching"))
    failed = [i for i in instances if i["status"] in ("failed", "terminated")]
    diagnostic = None
    if failed:
        reasons = sorted({i["status_reason"] or i["status"] for i in failed})
        diagnostic = FulfilmentDiagnostic(
            category=DiagnosticCategory.UNKNOWN,
            summary=f"{len(failed)} sandbox(es) not running: {'; '.join(reasons)[:200]}",
            occurred_at=datetime.now(timezone.utc),
        )
    state: FulfilmentState
    if running >= target:
        state, message = "fulfilled", f"{running}/{target} sandboxes running"
    elif len(failed) >= target:
        state, message = "failed", f"{len(failed)}/{target} sandboxes failed"
    elif pending:
        state, message = "in_progress", f"{running}/{target} running, {pending} starting"
    else:
        state, message = "partial", f"{running}/{target} running, {len(failed)} failed"
    return ProviderFulfilment(
        state=state,
        message=message,
        target_units=target,
        fulfilled_units=running,
        running_count=running,
        pending_count=pending,
        failed_count=len(failed),
        final=False,
        diagnostic=diagnostic,
    )


__all__ = ["GUEST_RUNNING", "fulfilment_for", "reportable_ip", "sandbox_machine_status"]

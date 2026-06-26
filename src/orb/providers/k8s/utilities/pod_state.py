"""Pure helpers for translating ``V1Pod`` snapshots into ORB status strings.

Shared between :class:`~orb.providers.k8s.handlers.base_handler.K8sHandlerBase`
and :class:`~orb.providers.k8s.watch.watcher.K8sWatcher` so the list-fed
and cache-fed code paths produce identical per-instance dicts downstream.

The functions are intentionally pure (no SDK imports, no logging) and
operate on the duck-typed ``conditions`` / ``container_statuses``
objects the kubernetes client returns.  Both handler instance dicts and
watcher cache snapshots compute their ``status`` / ``status_reason``
fields by calling into this module.
"""

from __future__ import annotations

from typing import Any, Optional


def is_pod_ready(conditions: list[Any]) -> bool:
    """Return ``True`` iff ``conditions`` has a ``Ready=True`` entry."""
    for cond in conditions:
        ctype = getattr(cond, "type", None)
        cstatus = getattr(cond, "status", None)
        if ctype == "Ready" and cstatus == "True":
            return True
    return False


def pod_status_string(phase: Optional[str], ready: bool) -> str:
    """Map ``pod.status.phase`` (+ readiness) to an ORB instance-status string.

    The string set mirrors the AWS provider's EC2 instance statuses so
    the downstream domain code (fulfilment math, status display) does
    not need to special-case kubernetes phases.

    * ``Pending``  -> ``"pending"``
    * ``Running`` (not ready)  -> ``"starting"``
    * ``Running`` (ready)      -> ``"running"``
    * ``Succeeded``            -> ``"running"``  (job-style success)
    * ``Failed``               -> ``"failed"``
    * ``Unknown``/None         -> ``"pending"``
    """
    if phase == "Running":
        return "running" if ready else "starting"
    if phase == "Succeeded":
        return "running"
    if phase == "Failed":
        return "failed"
    return "pending"


def extract_status_reason(
    container_statuses: list[Any],
    conditions: list[Any],
) -> Optional[str]:
    """Best-effort extraction of a human-readable status reason.

    Order of preference: terminated container reason, waiting container
    reason, ``PodScheduled=False`` condition reason.
    """
    for cs in container_statuses:
        state = getattr(cs, "state", None)
        if state is None:
            continue
        terminated = getattr(state, "terminated", None)
        if terminated is not None:
            reason = getattr(terminated, "reason", None)
            if reason:
                return str(reason)
        waiting = getattr(state, "waiting", None)
        if waiting is not None:
            reason = getattr(waiting, "reason", None)
            if reason:
                return str(reason)
    for cond in conditions:
        ctype = getattr(cond, "type", None)
        cstatus = getattr(cond, "status", None)
        reason = getattr(cond, "reason", None)
        if ctype == "PodScheduled" and cstatus == "False" and reason:
            return str(reason)
    return None


__all__ = [
    "extract_status_reason",
    "is_pod_ready",
    "pod_status_string",
]

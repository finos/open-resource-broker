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

# Provider-API types whose pods are managed by a controller that will
# automatically restart Succeeded pods.  For these types the ``Succeeded``
# phase is transient — the controller reconciliation loop will respawn the
# pod almost immediately — so ORB must not surface it as ``terminated``.
# Bare pods (``Pod``) and ``Job``-owned pods have no such controller: once
# they reach ``Succeeded`` they stay there, and ORB should treat them as
# ``terminated`` so the fulfilment math and status display reflect reality.
_CONTROLLER_RESPAWNS_SUCCEEDED: frozenset[str] = frozenset({"Deployment", "StatefulSet"})

# Container waiting reasons that indicate a terminal configuration error.
FATAL_WAITING_REASONS: frozenset[str] = frozenset(
    {
        "InvalidImageName",
        "ImagePullBackOff",
        "ErrImagePull",
        "CreateContainerConfigError",
        "CreateContainerError",
        "CrashLoopBackOff",
    }
)


def is_pod_ready(conditions: list[Any]) -> bool:
    """Return ``True`` iff ``conditions`` has a ``Ready=True`` entry."""
    for cond in conditions:
        ctype = getattr(cond, "type", None)
        cstatus = getattr(cond, "status", None)
        if ctype == "Ready" and cstatus == "True":
            return True
    return False


def pod_status_string(
    phase: Optional[str],
    ready: bool,
    *,
    provider_api: Optional[str] = None,
) -> str:
    """Map ``pod.status.phase`` (+ readiness) to an ORB instance-status string.

    The string set mirrors the AWS provider's EC2 instance statuses so
    the downstream domain code (fulfilment math, status display) does
    not need to special-case kubernetes phases.

    * ``Pending``  -> ``"pending"``
    * ``Running`` (not ready)  -> ``"starting"``
    * ``Running`` (ready)      -> ``"running"``
    * ``Succeeded`` (Deployment / StatefulSet) -> ``"running"``
      The controller will respawn the pod; ``Succeeded`` is transient here.
    * ``Succeeded`` (Pod / Job / unknown) -> ``"terminated"``
      Run-to-completion semantics: the pod finished and will not restart.
    * ``Failed``               -> ``"failed"``
    * ``Unknown``/None         -> ``"pending"``
    """
    if phase == "Running":
        return "running" if ready else "starting"
    if phase == "Succeeded":
        # Controller-managed pods (Deployment, StatefulSet) are respawned by
        # their controller when they complete — ``Succeeded`` is a transient
        # phase here, not a terminal one.  Return ``"running"`` so the
        # fulfilment math does not count them as lost capacity until the
        # controller has had time to restart them.
        #
        # Bare pods and Job pods run to completion exactly once.  Mapping
        # them to ``"terminated"`` lets check_hosts_status and the
        # orchestrator surface the correct state immediately.
        if provider_api in _CONTROLLER_RESPAWNS_SUCCEEDED:
            return "running"
        return "terminated"
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


def is_fatal_waiting_reason(reason: Optional[str]) -> bool:
    """Return ``True`` when ``reason`` is a terminal container waiting error."""
    return bool(reason) and reason in FATAL_WAITING_REASONS


__all__ = [
    "FATAL_WAITING_REASONS",
    "extract_status_reason",
    "is_fatal_waiting_reason",
    "is_pod_ready",
    "pod_status_string",
]

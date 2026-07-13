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
    init_container_statuses: Optional[list[Any]] = None,
) -> Optional[str]:
    """Best-effort extraction of a human-readable status reason.

    Order of preference:
    1. Terminated container reason (main containers).
    2. Waiting container reason (main containers) — catches CrashLoopBackOff,
       ImagePullBackOff, etc.
    3. Fatal waiting reason from init containers — catches
       ``Init:ImagePullBackOff``, ``Init:ErrImagePull``, etc. that keep the
       pod stuck in Pending with no visible reason on the main containers.
    4. ``PodScheduled=False`` condition reason.
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
    # Inspect init-container statuses for fatal waiting reasons.  These
    # surface as ``Init:<reason>`` in kubectl but the raw waiting reason is
    # available on the init container status directly.
    for ics in init_container_statuses or []:
        state = getattr(ics, "state", None)
        if state is None:
            continue
        waiting = getattr(state, "waiting", None)
        if waiting is not None:
            reason = getattr(waiting, "reason", None)
            if reason and reason in FATAL_WAITING_REASONS:
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


# Minimum restart count before a pod is considered to be in a persistent
# failure loop.  A single restart can be an innocuous transient event
# (e.g. OOMKilled from a one-off spike); two or more restarts with a
# non-zero exit code indicate a recurring crash.
_CRASH_RESTART_THRESHOLD = 2


def is_crash_loop_or_repeated_failure(
    container_statuses: list[Any],
    *,
    restart_threshold: int = _CRASH_RESTART_THRESHOLD,
    restart_policy: Optional[str] = None,
) -> bool:
    """Return ``True`` when a container shows repeated crash-loop behaviour.

    Detects two patterns that can both cause perpetual ``in_progress``
    status when a container briefly cycles back to ``Running`` between
    crash iterations:

    1. The current container state is ``Waiting`` with ``CrashLoopBackOff``.
       This is Kubernetes' own back-off signal that the container keeps
       failing; it is always treated as fatal regardless of restart policy.
    2. ``restart_count`` has reached *restart_threshold* AND
       ``last_state.terminated.exit_code`` is non-zero — meaning the
       container exited abnormally at least twice.  This fires even during
       the brief ``Running`` window between crashes when
       ``CrashLoopBackOff`` is not yet showing in the current state.

    The restart-count heuristic (pattern 2) is skipped when
    ``restart_policy == "OnFailure"``: there, repeated restarts with a
    non-zero exit code are the operator's intended retry semantics, not a
    crash loop, so only Kubernetes' own ``CrashLoopBackOff`` signal (pattern
    1) is treated as fatal.  For ``Always`` / ``Never`` (the ORB defaults for
    controller-backed and bare/Job pods) both patterns apply.
    """
    skip_restart_count = restart_policy == "OnFailure"
    for cs in container_statuses:
        restart_count = int(getattr(cs, "restart_count", 0) or 0)

        # Fast path: current state is already CrashLoopBackOff.  Always fatal.
        state = getattr(cs, "state", None)
        if state is not None:
            waiting = getattr(state, "waiting", None)
            if waiting is not None:
                reason = getattr(waiting, "reason", None)
                if reason == "CrashLoopBackOff":
                    return True

        # Detect crash loop during the brief Running window between restarts:
        # check last_state.terminated for a non-zero exit code combined with
        # an accumulated restart count at or above the threshold.  Skipped for
        # OnFailure pods, where such restarts are the intended retry behaviour.
        if not skip_restart_count and restart_count >= restart_threshold:
            last_state = getattr(cs, "last_state", None)
            if last_state is not None:
                last_terminated = getattr(last_state, "terminated", None)
                if last_terminated is not None:
                    exit_code = getattr(last_terminated, "exit_code", None)
                    if isinstance(exit_code, int) and exit_code != 0:
                        return True

    return False


__all__ = [
    "FATAL_WAITING_REASONS",
    "extract_status_reason",
    "is_crash_loop_or_repeated_failure",
    "is_fatal_waiting_reason",
    "is_pod_ready",
    "pod_status_string",
]

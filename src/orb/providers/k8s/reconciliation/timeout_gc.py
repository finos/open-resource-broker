"""Pod-stuck-in-Pending timeout detection and deletion helpers.

Pure-transform functions invoked by handler ``check_hosts_status`` paths.
When a pod has been Pending for longer than
:attr:`K8sProviderConfig.pod_timeout_seconds`, the handler
rewrites the per-instance dict in place:

* ``status`` is set to ``"terminated"`` so HostFactory sees a final
  state and the fulfilment math (running / pending / failed counters)
  stops treating the pod as in-flight;
* ``provider_data["unschedulable_reason"]`` is filled from the first
  meaningful ``status.conditions`` entry — typically the
  ``PodScheduled=False`` reason (``"Unschedulable"``).  Falls back to
  the original ``status_reason`` when no condition reason is present.

When :attr:`K8sProviderConfig.delete_timed_out_pods` is ``True``
(the default), the timeout GC also **deletes** the stuck pod via
``CoreV1Api.delete_namespaced_pod`` with ``grace_period_seconds=0``.
This prevents pods leaking indefinitely on busy HPC clusters.
Set ``delete_timed_out_pods = False`` to revert to the old read-only
behaviour (status rewrite only, no deletion).

The module exposes:

* :func:`is_pod_timed_out` — boolean predicate on a single instance dict.
* :func:`apply_pod_timeout` — pure transform that rewrites timed-out
  entries; no mutation of the input.
* :func:`delete_timed_out_pod_async` — async helper that deletes a single
  timed-out pod via the Kubernetes ``CoreV1Api``; 404 is tolerated.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.base.ports import LoggingPort

# Default condition reason emitted when scheduling is blocked.  The
# kubelet uses the exact string "Unschedulable" so this is a stable
# label rather than a magic string.
_UNSCHEDULABLE_REASON_DEFAULT = "Unschedulable"


def is_pod_timed_out(
    instance: dict[str, Any],
    *,
    pod_timeout_seconds: float,
    now: Optional[float] = None,
) -> bool:
    """Return ``True`` when ``instance`` represents a pod stuck in Pending.

    "Stuck" = ORB status is ``pending`` or ``starting`` (i.e. not yet
    Running, Succeeded, or Failed) AND the pod has been alive for
    longer than ``pod_timeout_seconds``.  Pod age is read from the
    ``launch_time`` field on the instance dict — the ISO timestamp the
    kubernetes ``status.start_time`` SDK field renders as.  When the
    age cannot be parsed (no launch_time, malformed value) the function
    returns ``False`` so a missing timestamp never falsely terminates a
    pod.

    The ``now`` parameter is exposed so tests can pin the clock.
    Production callers pass ``None`` and the function uses
    :func:`time.time` (wall-clock — pod start times come from the
    apiserver as wall-clock too).
    """
    status = instance.get("status")
    if status not in ("pending", "starting"):
        return False

    launch_time_str = instance.get("launch_time")
    if not isinstance(launch_time_str, str) or not launch_time_str:
        return False

    pod_start = _parse_iso_timestamp(launch_time_str)
    if pod_start is None:
        return False

    current = now if now is not None else time.time()
    age = current - pod_start
    return age >= pod_timeout_seconds


def apply_pod_timeout(
    instances: list[dict[str, Any]],
    *,
    pod_timeout_seconds: float,
    now: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Return a new instance list with timed-out pods rewritten.

    Each timed-out entry has:

    * ``status`` rewritten to ``"terminated"``;
    * ``status_reason`` set to the original condition reason (or
      ``"Unschedulable"`` as a defensive default);
    * ``provider_data["unschedulable_reason"]`` populated with the
      same reason so downstream consumers can surface the cause
      without re-parsing the original pod conditions.

    Non-timed-out entries are returned unchanged (same dict identity)
    so callers that iterate after timeout application do not pay a
    copy cost for the common path.
    """
    if pod_timeout_seconds <= 0 or not instances:
        return list(instances)

    result: list[dict[str, Any]] = []
    for instance in instances:
        if not is_pod_timed_out(
            instance,
            pod_timeout_seconds=pod_timeout_seconds,
            now=now,
        ):
            result.append(instance)
            continue
        result.append(_rewrite_timed_out_instance(instance))
    return result


def _rewrite_timed_out_instance(instance: dict[str, Any]) -> dict[str, Any]:
    """Build a new dict with timeout fields set; do not mutate the input."""
    reason = instance.get("status_reason") or _UNSCHEDULABLE_REASON_DEFAULT
    rewritten = dict(instance)
    rewritten["status"] = "terminated"
    rewritten["status_reason"] = reason
    provider_data = dict(rewritten.get("provider_data") or {})
    provider_data["unschedulable_reason"] = reason
    provider_data["timed_out"] = True
    rewritten["provider_data"] = provider_data
    return rewritten


async def delete_timed_out_pod_async(
    core_v1: Any,
    *,
    name: str,
    namespace: str,
    reason: str,
    logger: "LoggingPort",
) -> None:
    """Delete a single timed-out pod via ``CoreV1Api.delete_namespaced_pod``.

    Runs the blocking SDK call inside :func:`asyncio.to_thread` so the
    event loop is not blocked.  A ``404 Not Found`` response (pod already
    gone) is silently swallowed and logged at DEBUG level — this avoids a
    double-delete error when two concurrent check paths both decide the
    same pod is timed out.  Any other exception is caught, logged at
    WARNING level, and not re-raised so a single deletion failure does not
    interrupt the broader ``check_hosts_status`` scan.

    Args:
        core_v1:   A live ``kubernetes.client.CoreV1Api`` instance.
        name:      Pod name.
        namespace: Pod namespace.
        reason:    Human-readable reason string (from ``pod.status.conditions``
                   or the ``status_reason`` fallback).  Included in the INFO
                   log entry on successful deletion.
        logger:    Injected :class:`~orb.domain.base.ports.LoggingPort`
                   for structured log output.
    """

    def _delete() -> None:
        try:
            from kubernetes.client import V1DeleteOptions as _V1DeleteOptions  # noqa: PLC0415
        except ImportError:  # pragma: no cover — extra not installed
            logger.warning(
                "kubernetes SDK not installed; cannot delete timed-out pod %s/%s",
                namespace,
                name,
            )
            return

        try:
            core_v1.delete_namespaced_pod(
                name=name,
                namespace=namespace,
                body=_V1DeleteOptions(grace_period_seconds=0),
            )
            logger.info(
                "Deleted timed-out pod %s/%s (reason: %s)",
                namespace,
                name,
                reason,
            )
        except Exception as exc:  # noqa: BLE001
            # 404 — pod was already removed by another path; not an error.
            status = getattr(exc, "status", None)
            if status == 404:
                logger.debug(
                    "Timed-out pod %s/%s already gone (404); skipping delete.",
                    namespace,
                    name,
                )
                return
            logger.warning(
                "Failed to delete timed-out pod %s/%s: %s",
                namespace,
                name,
                exc,
                exc_info=True,
            )

    await asyncio.to_thread(_delete)


def _parse_iso_timestamp(value: str) -> Optional[float]:
    """Best-effort parse of a kubernetes ``start_time`` string.

    The kubernetes SDK renders ``status.start_time`` as either a Python
    ``datetime`` (which ``str`` turns into ``"2026-06-19 12:34:56+00:00"``)
    or an RFC 3339 string (``"2026-06-19T12:34:56Z"``).  We accept both
    shapes and fall back to ``None`` on anything else.
    """
    candidate = value.strip()
    if not candidate:
        return None
    # ``datetime.fromisoformat`` in 3.11+ accepts both space and 'T'
    # separators and ``+00:00`` offsets; the trailing ``Z`` shorthand
    # still requires manual replacement.
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


__all__ = [
    "apply_pod_timeout",
    "delete_timed_out_pod_async",
    "is_pod_timed_out",
]

"""Pod-state translation helpers for Kubernetes provider handlers.

Converts Kubernetes SDK objects (``V1Pod``) and cached pod snapshots
(:class:`~orb.providers.k8s.watch.pod_state_cache.PodState`) into the
flat instance-dict shape that ORB expects from every provider handler.

The dict mirrors the AWS provider's ``_format_instance_data`` output —
flat snake_case fields plus a ``provider_data`` block for per-handler
bookkeeping.  Shared by every concrete handler so the list-fed and the
cache-fed read paths produce identical dicts downstream.

Separating this logic into a standalone module means future handlers do
not need to inherit from :class:`K8sHandlerBase` to get correct
translation — they can call these functions directly.

Field semantics vs AWS
----------------------
* ``image_id``      — container image:tag of the pod's first (primary)
  container; mirrors the AMI ID field in AWS.
* ``instance_type`` — ``"k8s/<provider_api>"`` (e.g. ``"k8s/Pod"``)
  so UI grouping/filtering works identically to synthetic-terminated
  entries produced by the handler registry.
* ``public_ip``     — ``None``.  ``status.host_ip`` is the *node* IP,
  not a publicly routable address.  It is preserved in
  ``provider_data["host_ip"]`` so nothing is lost.
* ``launch_time``   — ISO 8601 string (``"2026-06-19T12:34:56+00:00"``)
  produced via :meth:`datetime.isoformat`; compatible with the
  :func:`_parse_iso_timestamp` helper in ``timeout_gc.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from orb.providers.k8s.utilities.pod_state import (
    extract_status_reason,
    is_fatal_waiting_reason,
    is_pod_ready,
    pod_status_string,
)


def _to_iso8601(start_time: Any) -> Optional[str]:
    """Normalise a kubernetes ``status.start_time`` value to ISO 8601.

    The SDK field is a ``datetime`` object when the apiserver returns a
    timestamp and ``None`` when the pod has not started yet.  ``str()``
    on a ``datetime`` uses a space separator (``"2026-06-19 12:34:56+00:00"``)
    which is technically RFC 3339 but not strict ISO 8601.
    :meth:`datetime.isoformat` always uses ``"T"`` and is the canonical
    form consumed by :func:`_parse_iso_timestamp` in ``timeout_gc.py``.

    Strings that arrive already formatted (e.g. from the cache) are
    returned unchanged.
    """
    if start_time is None:
        return None
    if isinstance(start_time, datetime):
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        return start_time.isoformat()
    # Already a string (e.g. from PodState.start_time); return as-is.
    return str(start_time)


def instance_dict_for_pod(
    pod: Any,
    namespace: str,
    *,
    provider_api: str,
    node_state_cache: Optional[Any] = None,
    logger: Optional[Any] = None,
) -> dict[str, Any]:
    """Convert a ``V1Pod`` SDK object to the per-instance dict shape ORB expects.

    Args:
        pod: A Kubernetes SDK ``V1Pod`` object (or any object with the same
            attribute structure).
        namespace: The namespace the pod belongs to.
        provider_api: The provider-API key (e.g. ``"Pod"``), forwarded to
            :func:`pod_status_string` for context-aware status derivation.
        node_state_cache: Optional node-state cache.  When provided and the
            pod has been scheduled, per-node metadata (instance type, zone,
            capacity type) is added to ``provider_data``.
        logger: Optional logger for ``Succeeded`` pod warnings.  When
            ``None``, the warning is silently suppressed.

    Returns:
        A flat instance dict with ``provider_data`` populated.
    """
    metadata = getattr(pod, "metadata", None)
    status = getattr(pod, "status", None)
    spec = getattr(pod, "spec", None)

    name = getattr(metadata, "name", "") if metadata is not None else ""
    labels = dict(getattr(metadata, "labels", None) or {}) if metadata is not None else {}
    phase = getattr(status, "phase", None) if status is not None else None
    pod_ip = getattr(status, "pod_ip", None) if status is not None else None
    host_ip = getattr(status, "host_ip", None) if status is not None else None
    node_name = getattr(spec, "node_name", None) if spec is not None else None
    start_time = getattr(status, "start_time", None) if status is not None else None
    conditions = list(getattr(status, "conditions", None) or []) if status is not None else []
    container_statuses = (
        list(getattr(status, "container_statuses", None) or []) if status is not None else []
    )

    # Derive image_id from the first (primary) container in the pod spec.
    # Falls back to None when the spec is absent or containers is empty.
    containers = list(getattr(spec, "containers", None) or []) if spec is not None else []
    image_id: Optional[str] = None
    if containers:
        raw_image = getattr(containers[0], "image", None)
        image_id = str(raw_image) if raw_image else None

    ready = is_pod_ready(conditions)
    status_str = pod_status_string(phase, ready, provider_api=provider_api)
    status_reason = extract_status_reason(container_statuses, conditions)

    # Escalate Pending pods with a fatal waiting reason to "failed".
    if status_str in ("pending", "starting") and is_fatal_waiting_reason(status_reason):
        status_str = "failed"

    if phase == "Succeeded":
        if status_str == "running":
            if logger is not None:
                logger.warning(
                    "Pod %s reached Succeeded under %s — controller will respawn; "
                    "treating as running until the new pod is ready",
                    name,
                    provider_api,
                )
        elif status_reason is None:
            status_reason = "Container completed successfully"

    # DisruptionTarget condition — Karpenter preemption signal.
    disrupted_reason: Optional[str] = None
    disrupted_message: Optional[str] = None
    for cond in conditions:
        if (
            getattr(cond, "type", None) == "DisruptionTarget"
            and getattr(cond, "status", None) == "True"
        ):
            disrupted_reason = str(getattr(cond, "reason", None) or "")
            disrupted_message = str(getattr(cond, "message", None) or "")
            break

    restart_count: int = sum(int(getattr(cs, "restart_count", 0) or 0) for cs in container_statuses)

    provider_data: dict[str, Any] = {
        "namespace": namespace,
        "node_name": node_name,
        # host_ip is the node's IP — preserved here for diagnostics but NOT
        # surfaced as public_ip (which implies an internet-routable address).
        "host_ip": host_ip,
        "phase": phase,
        "ready": ready,
        "restart_count": restart_count,
        "disrupted_reason": disrupted_reason,
        "disrupted_message": disrupted_message,
    }
    if node_name and node_state_cache is not None:
        node_state = node_state_cache.get(node_name)
        if node_state is not None:
            provider_data["node_instance_type"] = node_state.instance_type
            provider_data["node_zone"] = node_state.zone
            provider_data["node_capacity_type"] = node_state.capacity_type

    return {
        "instance_id": name,
        "resource_id": name,
        "name": name,
        "status": status_str,
        "status_reason": status_reason,
        "private_ip": pod_ip,
        # Pods do not have internet-routable public IPs; host_ip is the node
        # IP and is available in provider_data["host_ip"] above.
        "public_ip": None,
        "launch_time": _to_iso8601(start_time),
        "instance_type": f"k8s/{provider_api}",
        "image_id": image_id,
        "subnet_id": None,
        "security_group_ids": [],
        "vpc_id": None,
        "tags": labels,
        "price_type": None,
        "provider_api": provider_api,
        "provider_data": provider_data,
        "metadata": {},
    }


def instance_dict_for_state(
    state: Any,
    *,
    provider_api: str,
    node_state_cache: Optional[Any] = None,
) -> dict[str, Any]:
    """Convert a cached :class:`PodState` into the instance-dict shape.

    Mirrors :func:`instance_dict_for_pod` so the list-fed and cache-fed code
    paths produce identical dicts downstream.

    Args:
        state: A pod snapshot from :class:`PodStateCache`.
        provider_api: The provider-API key (e.g. ``"Pod"``).
        node_state_cache: Optional node-state cache for per-node metadata
            enrichment.

    Returns:
        A flat instance dict with ``provider_data`` populated.
    """
    # Read image_id from the cached state.  The field was added to PodState
    # after the initial cache design; getattr with a None default keeps the
    # function compatible with any older PodState instances that lack it.
    image_id: Optional[str] = getattr(state, "image_id", None) or None

    provider_data: dict[str, Any] = {
        "namespace": state.namespace,
        "node_name": state.node_name,
        # host_ip is the node's IP — preserved here for diagnostics but NOT
        # surfaced as public_ip (which implies an internet-routable address).
        "host_ip": state.host_ip,
        "phase": state.phase,
        "ready": state.ready,
        "restart_count": state.restart_count,
        "disrupted_reason": state.disrupted_reason,
        "disrupted_message": state.disrupted_message,
    }
    if state.node_name and node_state_cache is not None:
        node_state = node_state_cache.get(state.node_name)
        if node_state is not None:
            provider_data["node_instance_type"] = node_state.instance_type
            provider_data["node_zone"] = node_state.zone
            provider_data["node_capacity_type"] = node_state.capacity_type

    return {
        "instance_id": state.pod_name,
        "resource_id": state.pod_name,
        "name": state.pod_name,
        "status": state.status,
        "status_reason": state.status_reason,
        "private_ip": state.pod_ip,
        # Pods do not have internet-routable public IPs; host_ip is the node
        # IP and is available in provider_data["host_ip"] above.
        "public_ip": None,
        "launch_time": _to_iso8601(state.start_time),
        "instance_type": f"k8s/{provider_api}",
        "image_id": image_id,
        "subnet_id": None,
        "security_group_ids": [],
        "vpc_id": None,
        "tags": dict(state.labels),
        "price_type": None,
        "provider_api": provider_api,
        "provider_data": provider_data,
        "metadata": {},
    }


__all__ = [
    "instance_dict_for_pod",
    "instance_dict_for_state",
]

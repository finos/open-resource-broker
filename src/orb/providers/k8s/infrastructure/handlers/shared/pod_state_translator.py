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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.providers.k8s.utilities.pod_state import (
    extract_status_reason,
    is_fatal_waiting_reason,
    is_pod_ready,
    pod_status_string,
)

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.providers.k8s.watch.node_state_cache import K8sNodeStateCache
    from orb.providers.k8s.watch.pod_state_cache import PodState


def instance_dict_for_pod(
    pod: Any,
    namespace: str,
    *,
    provider_api: str,
    node_state_cache: Optional["K8sNodeStateCache"] = None,
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
        else:
            if status_reason is None:
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
        "public_ip": host_ip,
        "launch_time": str(start_time) if start_time is not None else None,
        "instance_type": "",
        "image_id": "",
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
    state: "PodState",
    *,
    provider_api: str,
    node_state_cache: Optional["K8sNodeStateCache"] = None,
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
    provider_data: dict[str, Any] = {
        "namespace": state.namespace,
        "node_name": state.node_name,
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
        "public_ip": state.host_ip,
        "launch_time": state.start_time,
        "instance_type": "",
        "image_id": "",
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

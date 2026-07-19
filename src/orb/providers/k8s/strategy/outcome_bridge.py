"""Outcome-to-``ProviderResult`` bridge for the Kubernetes provider.

Extracted from :mod:`orb.providers.k8s.strategy.k8s_provider_strategy` so the
strategy shell stays focused on lifecycle orchestration.  The three helpers
here translate the typed :class:`OperationOutcome` union the k8s handlers
produce into the shared :class:`ProviderResult` envelope the provisioning
service consumes.

The strategy module re-imports these names so existing callers and tests that
reference them via ``k8s_provider_strategy`` keep working unchanged.
"""

from __future__ import annotations

from typing import Any, Optional

from orb.domain.base.operation_outcome import OperationOutcome
from orb.providers.base.strategy import ProviderResult

__all__ = [
    "_all_instances_terminal",
    "_build_provider_result_data",
    "_outcome_to_provider_result",
]


def _build_provider_result_data(
    *,
    resource_ids: list[str],
    metadata: Optional[dict[str, Any]] = None,
    tracking_request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build the ``ProviderResult.data`` dict for k8s outcomes.

    Resource-vs-machine model (Kubernetes):

    * **Pod handler** — every Pod IS its own resource.  Acquire emits
      ``resource_ids == machine_ids == [pod_name, ...]`` (1:1).  The
      handler also surfaces a per-machine ``instances`` list with the
      pod's ``status``, ``image_id`` and so on, populated lazily by the
      status resolver.
    * **Deployment / StatefulSet / Job handler** — the workload controller
      is the *resource* (1 entry: ``[deployment_name]``); the Pods it
      spawns are the *machines* (N entries, populated by the status
      resolver).  Acquire emits ``machine_ids=[]`` because the
      controller has not yet scheduled any pods.

    The bridge therefore propagates whatever the handler put under
    ``metadata['instances']`` verbatim (it carries the authoritative
    ``resource_id`` ↔ ``machine_id`` mapping per pod) and otherwise
    leaves ``instances`` empty so that downstream machine creation is
    driven by a subsequent status read rather than by manufactured rows.
    """
    meta_dict = dict(metadata or {})
    # ``machine_ids`` are the per-pod identifiers (1 per machine row);
    # ``resource_ids`` are the per-controller (or per-pod for the Pod
    # handler) identifiers.  Both are propagated through ``provider_data``
    # so the application layer can reason about them independently.
    machine_ids = list(meta_dict.get("machine_ids") or [])
    instances = list(meta_dict.get("instances") or [])

    data: dict[str, Any] = {
        "resource_ids": resource_ids,
        "instances": instances,
        # ``instance_ids`` consumed by deprovisioning / sync paths that
        # want the per-pod identifiers.  Falls back to ``resource_ids``
        # for the Pod handler (where resource = machine) when the
        # handler did not put machine_ids in metadata.
        "instance_ids": machine_ids or resource_ids,
        "provider_data": meta_dict,
    }
    if tracking_request_id is not None:
        data["tracking_request_id"] = tracking_request_id
    return data


def _all_instances_terminal(instances: list[dict[str, Any]]) -> bool:
    """Return True when every instance dict has reached a non-pending state.

    Used by the bridge to detect synchronous completions: when an Accepted
    outcome carries ``pending_resource_ids`` but ``metadata['instances']``
    already shows every pod as running, succeeded, or terminated, the request
    has effectively completed and ``fulfillment_final`` should be set so the
    provisioning service does not keep it in IN_PROGRESS indefinitely.

    An empty instances list is not considered terminal — the status resolver
    has not yet populated instance data, so we cannot make a determination.
    """
    if not instances:
        return False
    terminal_states = {"running", "succeeded", "terminated"}
    return all(inst.get("status") in terminal_states for inst in instances)


def _outcome_to_provider_result(
    outcome: OperationOutcome, *, fallback_operation: str
) -> ProviderResult:
    """Translate an :class:`OperationOutcome` into a :class:`ProviderResult`.

    Used by ``execute_operation`` to bridge the kubernetes provider's typed
    provisioning interface back to the shared ``ProviderOperation`` envelope
    that the provisioning orchestration service consumes.

    ``fulfillment_final=True`` is set in two cases:
    * ``Completed`` outcome — always terminal.
    * ``Accepted`` outcome where ``pending_resource_ids`` is non-empty AND
      every instance in ``metadata['instances']`` already has a
      running/terminal status.  This covers Pod handlers that schedule pods
      synchronously so the provisioning service does not keep the request
      in IN_PROGRESS waiting for a state transition that already happened.
    """
    from orb.domain.base.operation_outcome import (
        Accepted,
        Completed,
        Failed,
        RequiresFollowUp,
    )

    if isinstance(outcome, Failed):
        return ProviderResult.error_result(outcome.error, "OPERATION_FAILED").model_copy(
            update={
                "metadata": {
                    **(outcome.metadata or {}),
                    "operation": fallback_operation,
                    "provider": "k8s",
                    "recoverable": outcome.recoverable,
                }
            }
        )

    if isinstance(outcome, Accepted):
        meta = dict(outcome.metadata or {})
        pending = list(outcome.pending_resource_ids)
        if pending and _all_instances_terminal(list(meta.get("instances") or [])):
            # All pods are already in a terminal/running state at accept time —
            # promote to fulfillment_final so the provisioning service closes
            # the request without a redundant status poll.
            meta["fulfillment_final"] = True
        return ProviderResult.success_result(
            _build_provider_result_data(
                resource_ids=pending,
                metadata=meta,
                tracking_request_id=outcome.request_id,
            ),
            {"operation": fallback_operation, "provider": "k8s"},
        )

    if isinstance(outcome, Completed):
        # ``fulfillment_final=True`` signals to the provisioning service
        # that the request has reached a terminal state — without it the
        # request would stay IN_PROGRESS forever.
        meta = {**dict(outcome.metadata or {}), "fulfillment_final": True}
        return ProviderResult.success_result(
            _build_provider_result_data(
                resource_ids=list(outcome.resource_ids),
                metadata=meta,
            ),
            {"operation": fallback_operation, "provider": "k8s"},
        )

    if isinstance(outcome, RequiresFollowUp):
        ctx = outcome.context
        # Both follow-up variants carry an ID list; surface it as
        # ``resource_ids`` so the application layer keeps a handle on
        # what's still pending.  ``follow_up_kind`` and the typed context
        # ride along in ``provider_data`` for the background poller.
        pending_ids: list[str] = list(
            getattr(ctx, "pending_resource_ids", None)
            or getattr(ctx, "pending_instance_ids", None)
            or []
        )
        meta = {
            **dict(outcome.metadata or {}),
            "follow_up_kind": ctx.follow_up_kind,
            "provider_handle": getattr(ctx, "provider_handle", None),
            "expected_terminal_state": getattr(ctx, "expected_terminal_state", None),
        }
        return ProviderResult.success_result(
            _build_provider_result_data(
                resource_ids=pending_ids,
                metadata=meta,
            ),
            {"operation": fallback_operation, "provider": "k8s"},
        )

    return ProviderResult.error_result(
        f"Unknown OperationOutcome variant: {type(outcome).__name__}",
        "UNSUPPORTED_OUTCOME",
    )

"""Abort a request's in-flight provider provisioning through the CANCEL_PROVISIONING operation.

Callers that need to stop provisioning (a cancel, a cancel that lands while the
resources are being created, a deadline release of an empty resource) share
this one capability check and operation call; each maps the outcome to its own
errors and records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from orb.domain.base.operations import Operation, OperationType
from orb.domain.base.ports.provider_selection_port import ProviderSelectionPort


@dataclass(frozen=True)
class AbortOutcome:
    """Result of an abort attempt.

    Attributes:
        supported: False when the provider cannot abort in-flight provisioning.
        success: True when the provider reported the abort as done.
        outcomes: Per-resource outcomes the provider reported.
        members: Instances the provider listed for the resources after the abort.
        reason: Why the abort did not succeed, empty on success.
    """

    supported: bool
    success: bool
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    members: list[str] = field(default_factory=list)
    reason: str = ""


async def abort_provisioning(
    port: ProviderSelectionPort,
    provider_name: str,
    request_id: str,
    provider_api: Optional[str],
    resource_ids: list[str],
    instance_ids: Optional[list[str]] = None,
) -> AbortOutcome:
    """Abort the provisioning of ``resource_ids`` through the provider's CANCEL_PROVISIONING.

    A provider reports the capability only when its abort also terminates the
    instances the resources launched (fleets are deleted or cancelled with
    their instances, groups are force-deleted), so a successful abort leaves
    nothing of the resources running. The instances listed in
    ``AbortOutcome.members`` are those the provider found while aborting.

    Exceptions raised by the provider call propagate to the caller.
    """
    capabilities = port.get_strategy_capabilities(provider_name)
    supports = getattr(capabilities, "supports_operation", None)  # port returns Any
    if supports is None or not supports(OperationType.CANCEL_PROVISIONING):
        return AbortOutcome(
            supported=False,
            success=False,
            reason=f"provider '{provider_name}' cannot abort in-flight provisioning",
        )
    if not provider_api:
        return AbortOutcome(supported=True, success=False, reason="the request has no provider API")

    parameters: dict[str, Any] = {
        "request_id": request_id,
        "provider_api": provider_api,
        "resource_ids": list(resource_ids),
    }
    if instance_ids is not None:
        parameters["instance_ids"] = list(instance_ids)
    result = await port.execute_operation(
        provider_name,
        Operation(
            operation_type=OperationType.CANCEL_PROVISIONING,
            parameters=parameters,
            context={"correlation_id": request_id, "request_id": request_id},
        ),
    )

    data = getattr(result, "data", None)  # provider result shape
    resources = data.get("resources") if isinstance(data, dict) else None
    outcomes: list[dict[str, Any]] = []
    members: list[str] = []
    for entry in resources or []:
        members.extend(str(m) for m in entry.get("members") or [])
        outcomes.append({k: v for k, v in entry.items() if k != "members"})
    if not getattr(result, "success", False):
        reason = str(getattr(result, "error_message", "") or "provider reported a failure")
        return AbortOutcome(
            supported=True, success=False, outcomes=outcomes, members=members, reason=reason
        )
    return AbortOutcome(supported=True, success=True, outcomes=outcomes, members=members)

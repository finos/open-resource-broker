"""Provider fulfilment contract — domain value objects.

Every call to ``check_hosts_status`` on a provider handler MUST return a
``CheckHostsStatusResult`` that includes a ``ProviderFulfilment``.  The
application layer trusts this verdict exclusively — no count math, no
provider-specific key inspection.

See design doc: .claude/plans/PLAN-provider-fulfilment-contract.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from orb.domain.base.diagnostic import FulfilmentDiagnostic

# The four possible states a provider can report for a request.
#
# fulfilled  — target capacity met, all instances running, no failures.
# in_progress — still provisioning; poll again.
# partial    — some capacity met but the provider has given up on the rest
#              (e.g. instant fleet returned fewer instances than requested).
# failed     — provider could not fulfil the request at all.
FulfilmentState = Literal["fulfilled", "in_progress", "partial", "failed"]


@dataclass(frozen=True)
class ProviderFulfilment:
    """Provider-computed verdict on whether a request is fulfilled.

    Each provider knows its own API semantics (capacity units, instance
    count, weighted vCPU, native fulfilment state machines).  This object
    is the single contract the application layer trusts — no count math
    or provider-specific keys in shared services.

    Attributes:
        state: High-level verdict (see FulfilmentState).
        message: Human-readable summary for status display.
        target_units: Capacity units requested (may differ from instance count
            for weighted fleets/ASGs).  None for providers without the concept.
        fulfilled_units: Capacity units currently fulfilled.  None if unknown.
        running_count: Number of instances in the running state.  None if unknown.
        pending_count: Number of instances still starting/pending.  None if unknown.
        failed_count: Number of instances in a failure state.  None if unknown.
        final: Whether this verdict is the provider's *last word* on capacity —
            ``True`` only for synchronous providers (AWS RunInstances / instant
            fleet / MicroVM) whose launch API has already settled, so a
            ``partial`` state means no further capacity is coming and the
            request should terminalise immediately.  ``False`` (the default) for
            asynchronous providers such as Kubernetes, where a ``partial`` with
            ``pending_count == 0`` is a *transient* view (the pod list lags the
            controller's reconciliation intent) and must be allowed to heal via
            the PARTIAL_PENDING holding state.  Never infer finality from
            ``pending_count`` — that signal is provider-specific.
        diagnostic: Optional structured explanation of *why* the verdict is
            partial/failed (capacity, auth, validation, ...).  None when the
            provider has nothing to explain (e.g. a clean ``fulfilled``).
    """

    state: FulfilmentState
    message: str
    target_units: int | None = None
    fulfilled_units: int | None = None
    running_count: int | None = None
    pending_count: int | None = None
    failed_count: int | None = None
    final: bool = False
    diagnostic: FulfilmentDiagnostic | None = None


@dataclass(frozen=True)
class CheckHostsStatusResult:
    """Combined result of a ``check_hosts_status`` handler call.

    Bundles the per-instance details list (existing API surface) with the
    provider-computed fulfilment verdict so callers never need to re-derive
    fulfilment from raw instance counts.

    Attributes:
        instances: List of per-instance detail dicts (same format as the
            previous ``list[dict]`` return type from each handler).
        fulfilment: Provider's verdict on whether the request is fulfilled.
    """

    instances: list[dict]
    fulfilment: ProviderFulfilment

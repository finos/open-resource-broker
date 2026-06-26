"""Input/output dataclasses for all orchestrators."""

from __future__ import annotations

import base64
import dataclasses
import json
from typing import Any, Generic, Optional, TypeVar

from orb.application.machine.dto import MachineDTO

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Pagination envelope
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Paginated(Generic[T]):
    """Return shape for query handlers that slice an in-memory dataset.

    items                 — the page that satisfies (offset, limit) AFTER
                            any filter/sort has been applied.
    total_count           — total rows AFTER filters (the denominator the
                            client expects for "showing N of M").
    total_unfiltered      — total rows in the raw dataset before filters.
                            Optional; useful for UIs that want to show
                            "1 of 10000 templates match this filter".
    """

    items: list[T]
    total_count: int
    total_unfiltered: Optional[int] = None


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------

def encode_cursor(offset: int) -> str:
    """Encode an offset into an opaque, URL-safe base64 cursor string.

    Format: base64url({"offset": <int>})
    """
    payload = json.dumps({"offset": offset})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: Optional[str]) -> int:
    """Decode an opaque cursor string back to an integer offset.

    Returns 0 if *cursor* is None or cannot be decoded, so callers can always
    treat the return value as a valid offset.
    """
    if cursor is None:
        return 0
    try:
        payload = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(payload)
        return int(data.get("offset", 0))
    except Exception:  # noqa: BLE001
        return 0


@dataclasses.dataclass(frozen=True)
class AcquireMachinesInput:
    template_id: str
    requested_count: int
    wait: bool = False
    timeout_seconds: int = 300
    additional_data: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class AcquireMachinesOutput:
    request_id: str
    status: str
    machine_ids: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class GetRequestStatusInput:
    request_ids: list[str] = dataclasses.field(default_factory=list)
    all_requests: bool = False
    verbose: bool = False


@dataclasses.dataclass(frozen=True)
class GetRequestStatusOutput:
    requests: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class RequestStatusError:
    request_id: str
    error: str


@dataclasses.dataclass(frozen=True)
class ListRequestsInput:
    status: Optional[str] = None
    limit: int = 50
    sync: bool = False
    offset: int = 0
    template_id: Optional[str] = None
    request_type: Optional[str] = None
    # Server-side filtering / sorting / cursor pagination
    q: Optional[str] = None
    sort: Optional[str] = None
    cursor: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class ListRequestsOutput:
    requests: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    count: int = 0
    next_cursor: Optional[str] = None
    total_count: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class ReturnMachinesInput:
    machine_ids: list[str] = dataclasses.field(default_factory=list)
    all_machines: bool = False
    force: bool = False
    wait: bool = False
    timeout_seconds: int = 300


@dataclasses.dataclass(frozen=True)
class ReturnMachinesOutput:
    request_id: Optional[str]
    status: str
    message: str = ""
    skipped_machines: list[str] = dataclasses.field(default_factory=list)
    machine_ids: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class CancelRequestInput:
    request_id: str
    reason: str = "Cancelled via API"
    force: bool = False


@dataclasses.dataclass(frozen=True)
class CancelRequestOutput:
    request_id: str
    status: str
    requests: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class ListMachinesInput:
    status: Optional[str] = None
    provider_name: Optional[str] = None
    request_id: Optional[str] = None
    limit: int = 100
    offset: int = 0
    timestamp_format: Optional[str] = None
    # Server-side filtering / sorting / cursor pagination
    q: Optional[str] = None
    sort: Optional[str] = None
    cursor: Optional[str] = None
    # When True, refresh every machine on the returned page from the
    # provider. Off by default; the per-machine /status endpoint is the
    # preferred refresh path for the drawer.
    sync: bool = False


@dataclasses.dataclass(frozen=True)
class ListMachinesOutput:
    machines: list[MachineDTO] = dataclasses.field(default_factory=list)
    count: int = 0
    next_cursor: Optional[str] = None
    total_count: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class GetMachineInput:
    machine_id: str


@dataclasses.dataclass(frozen=True)
class GetMachineOutput:
    machine: Optional[MachineDTO]


@dataclasses.dataclass(frozen=True)
class SyncMachineInput:
    """Refresh a single machine's state from the provider before returning."""

    machine_id: str


@dataclasses.dataclass(frozen=True)
class SyncMachineOutput:
    """Result of a per-machine provider sync.

    ``synced`` is False when the machine exists in storage but the
    provider call failed or returned nothing; ``machine`` still holds
    the last-known state from storage in that case.
    """

    machine: Optional[MachineDTO]
    synced: bool
    error: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class ListTemplatesInput:
    active_only: bool = True
    provider_name: Optional[str] = None
    provider_api: Optional[str] = None
    limit: int = 50
    offset: int = 0
    # Server-side filtering / sorting / cursor pagination
    q: Optional[str] = None
    sort: Optional[str] = None
    cursor: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class ListTemplatesOutput:
    templates: list[Any] = dataclasses.field(default_factory=list)
    count: int = 0
    next_cursor: Optional[str] = None
    total_count: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class ListReturnRequestsInput:
    status: Optional[str] = None
    limit: int = 50
    offset: int = 0
    # Server-side filtering / sorting / cursor pagination
    q: Optional[str] = None
    sort: Optional[str] = None
    cursor: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class ListReturnRequestsOutput:
    requests: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    next_cursor: Optional[str] = None
    total_count: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class GetTemplateInput:
    template_id: str
    provider_name: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class GetTemplateOutput:
    template: Optional[Any] = None


@dataclasses.dataclass(frozen=True)
class CreateTemplateInput:
    template_id: str
    image_id: str
    provider_api: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    instance_type: Optional[str] = None
    tags: dict[str, str] = dataclasses.field(default_factory=dict)
    configuration: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class CreateTemplateOutput:
    template_id: str
    created: bool
    validation_errors: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class UpdateTemplateInput:
    template_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    instance_type: Optional[str] = None
    image_id: Optional[str] = None
    configuration: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class UpdateTemplateOutput:
    template_id: str
    updated: bool
    validation_errors: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class DeleteTemplateInput:
    template_id: str


@dataclasses.dataclass(frozen=True)
class DeleteTemplateOutput:
    template_id: str
    deleted: bool


@dataclasses.dataclass(frozen=True)
class ValidateTemplateInput:
    template_id: Optional[str] = None
    config: Optional[dict[str, Any]] = None


@dataclasses.dataclass(frozen=True)
class ValidateTemplateOutput:
    valid: bool
    errors: list[str] = dataclasses.field(default_factory=list)
    message: str = ""
    template_id: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class RefreshTemplatesInput:
    provider_name: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class RefreshTemplatesOutput:
    templates: list[Any] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class StopMachinesInput:
    machine_ids: list[str] = dataclasses.field(default_factory=list)
    all_machines: bool = False
    force: bool = False


@dataclasses.dataclass(frozen=True)
class StopMachinesOutput:
    stopped_machines: list[str] = dataclasses.field(default_factory=list)
    failed_machines: list[str] = dataclasses.field(default_factory=list)
    success: bool = True
    message: str = ""


@dataclasses.dataclass(frozen=True)
class StartMachinesInput:
    machine_ids: list[str] = dataclasses.field(default_factory=list)
    all_machines: bool = False


@dataclasses.dataclass(frozen=True)
class StartMachinesOutput:
    started_machines: list[str] = dataclasses.field(default_factory=list)
    failed_machines: list[str] = dataclasses.field(default_factory=list)
    success: bool = True
    message: str = ""


@dataclasses.dataclass(frozen=True)
class GetProviderHealthInput:
    provider_name: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class GetProviderHealthOutput:
    health: dict[str, Any] = dataclasses.field(default_factory=dict)
    message: str = ""


@dataclasses.dataclass(frozen=True)
class GetProviderConfigInput:
    pass


@dataclasses.dataclass(frozen=True)
class GetProviderConfigOutput:
    config: dict[str, Any] = dataclasses.field(default_factory=dict)
    message: str = ""


@dataclasses.dataclass(frozen=True)
class GetProviderMetricsInput:
    provider_name: Optional[str] = None
    timeframe: str = "24h"


@dataclasses.dataclass(frozen=True)
class GetProviderMetricsOutput:
    metrics: dict[str, Any] = dataclasses.field(default_factory=dict)
    message: str = ""


@dataclasses.dataclass(frozen=True)
class ListProvidersInput:
    provider_name: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class ListProvidersOutput:
    providers: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    count: int = 0
    selection_policy: str = ""
    message: str = ""


@dataclasses.dataclass(frozen=True)
class ListSchedulerStrategiesInput:
    pass


@dataclasses.dataclass(frozen=True)
class ListSchedulerStrategiesOutput:
    strategies: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    current_strategy: str = ""
    count: int = 0


@dataclasses.dataclass(frozen=True)
class GetSchedulerConfigInput:
    strategy_name: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class GetSchedulerConfigOutput:
    config: dict[str, Any] = dataclasses.field(default_factory=dict)
    message: str = ""


@dataclasses.dataclass(frozen=True)
class ListStorageStrategiesInput:
    pass


@dataclasses.dataclass(frozen=True)
class ListStorageStrategiesOutput:
    strategies: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    current_strategy: str = ""
    count: int = 0


@dataclasses.dataclass(frozen=True)
class GetStorageConfigInput:
    strategy_name: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class GetStorageConfigOutput:
    config: dict[str, Any] = dataclasses.field(default_factory=dict)
    message: str = ""


@dataclasses.dataclass(frozen=True)
class WatchRequestStatusInput:
    request_id: str


@dataclasses.dataclass(frozen=True)
class WatchRequestStatusOutput:
    request_id: str
    status: str
    terminal: bool = False
    requested_count: int = 0
    fulfilled_count: int = 0
    fulfilled_vcpus: int = 0
    od_vcpus: int = 0
    spot_vcpus: int = 0
    fulfilled_capacity: int = 0
    od_capacity: int = 0
    spot_capacity: int = 0
    od_machines: int = 0
    spot_machines: int = 0
    weighted: bool = False
    az_stats: dict[str, dict[str, int]] = dataclasses.field(default_factory=dict)
    created_at: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Dashboard summary
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class DashboardSummaryInput:
    """Input for the dashboard summary orchestrator. Reserved for future filters."""


@dataclasses.dataclass(frozen=True)
class RecentActivityItem:
    request_id: str
    status: str
    request_type: str
    template_id: str
    created_at: str  # ISO-8601
    successful_count: int
    requested_count: int


@dataclasses.dataclass(frozen=True)
class DashboardSummaryOutput:
    machines: dict[str, Any] = dataclasses.field(default_factory=dict)
    requests: dict[str, Any] = dataclasses.field(default_factory=dict)
    templates: dict[str, Any] = dataclasses.field(default_factory=dict)
    recent_activity: list[Any] = dataclasses.field(default_factory=list)

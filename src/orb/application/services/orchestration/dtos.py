"""Input/output dataclasses for all orchestrators."""

from __future__ import annotations

import dataclasses
from typing import Any, Optional

from orb.application.machine.dto import MachineDTO


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


@dataclasses.dataclass(frozen=True)
class ListRequestsOutput:
    requests: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    count: int = 0


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


@dataclasses.dataclass(frozen=True)
class ListMachinesOutput:
    machines: list[MachineDTO] = dataclasses.field(default_factory=list)
    count: int = 0


@dataclasses.dataclass(frozen=True)
class GetMachineInput:
    machine_id: str


@dataclasses.dataclass(frozen=True)
class GetMachineOutput:
    machine: Optional[MachineDTO]


@dataclasses.dataclass(frozen=True)
class ListTemplatesInput:
    active_only: bool = True
    provider_name: Optional[str] = None
    provider_api: Optional[str] = None
    limit: int = 50
    offset: int = 0


@dataclasses.dataclass(frozen=True)
class ListTemplatesOutput:
    templates: list[Any] = dataclasses.field(default_factory=list)
    count: int = 0


@dataclasses.dataclass(frozen=True)
class ListReturnRequestsInput:
    status: Optional[str] = None
    limit: int = 50


@dataclasses.dataclass(frozen=True)
class ListReturnRequestsOutput:
    requests: list[dict[str, Any]] = dataclasses.field(default_factory=list)


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
    provider_api: str
    image_id: str
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

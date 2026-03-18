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
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class GetRequestStatusInput:
    request_ids: list[str] = dataclasses.field(default_factory=list)
    all_requests: bool = False
    detailed: bool = False


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


@dataclasses.dataclass(frozen=True)
class ListRequestsOutput:
    requests: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class ReturnMachinesInput:
    machine_ids: list[str] = dataclasses.field(default_factory=list)
    all_machines: bool = False
    force: bool = False


@dataclasses.dataclass(frozen=True)
class ReturnMachinesOutput:
    request_id: Optional[str]
    status: str
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class CancelRequestInput:
    request_id: str
    reason: str = "Cancelled via API"


@dataclasses.dataclass(frozen=True)
class CancelRequestOutput:
    request_id: str
    status: str
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)
    requests: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class ListMachinesInput:
    status: Optional[str] = None
    provider_name: Optional[str] = None
    request_id: Optional[str] = None
    limit: int = 100
    offset: int = 0


@dataclasses.dataclass(frozen=True)
class ListMachinesOutput:
    machines: list[MachineDTO] = dataclasses.field(default_factory=list)


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
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


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
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class DeleteTemplateInput:
    template_id: str


@dataclasses.dataclass(frozen=True)
class DeleteTemplateOutput:
    template_id: str
    deleted: bool
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class ValidateTemplateInput:
    template_id: Optional[str] = None
    config: Optional[dict[str, Any]] = None


@dataclasses.dataclass(frozen=True)
class ValidateTemplateOutput:
    valid: bool
    errors: list[str] = dataclasses.field(default_factory=list)
    message: str = ""
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class RefreshTemplatesInput:
    provider_name: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class RefreshTemplatesOutput:
    templates: list[dict[str, Any]] = dataclasses.field(default_factory=list)


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

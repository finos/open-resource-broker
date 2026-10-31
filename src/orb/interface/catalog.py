"""Operation catalog — the single declaration of each domain operation.

Every logical operation the broker exposes (list machines, acquire machines,
get a template, and so on) is declared here exactly once as a
:class:`CatalogEntry`. An entry ties together the four things that were
previously restated in every interface adapter:

* the orchestrator that dispatches the operation through the CQRS buses,
* the input DTO the orchestrator consumes,
* the output DTO it returns,
* the :class:`ResponseFormattingService` call that renders that output into an
  :class:`InterfaceResponse` body, and
* the set of interfaces (CLI/REST/MCP/SDK) on which the operation currently
  routes through that orchestrator-and-formatter path.

The render field is a closure rather than a method name so the type checker can
verify both the formatter call and the output DTO's fields at the declaration
site. Adapters remain responsible for their own concerns that genuinely differ
per protocol — binding raw input into the declared input DTO and wrapping the
rendered body in a protocol envelope (HTTP status, MCP text wrap, CLI exit
code) — but the operation itself, and its canonical body shape, is declared
once here.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.create_template import CreateTemplateOrchestrator
from orb.application.services.orchestration.delete_template import DeleteTemplateOrchestrator
from orb.application.services.orchestration.dtos import (
    AcquireMachinesInput,
    AcquireMachinesOutput,
    CancelRequestInput,
    CancelRequestOutput,
    CreateTemplateInput,
    CreateTemplateOutput,
    DeleteTemplateInput,
    DeleteTemplateOutput,
    GetMachineInput,
    GetMachineOutput,
    GetProviderConfigInput,
    GetProviderConfigOutput,
    GetProviderHealthInput,
    GetProviderHealthOutput,
    GetProviderMetricsInput,
    GetProviderMetricsOutput,
    GetRequestStatusInput,
    GetRequestStatusOutput,
    GetTemplateInput,
    GetTemplateOutput,
    ListMachinesInput,
    ListMachinesOutput,
    ListProvidersInput,
    ListProvidersOutput,
    ListRequestsInput,
    ListRequestsOutput,
    ListReturnRequestsInput,
    ListReturnRequestsOutput,
    ListTemplatesInput,
    ListTemplatesOutput,
    RefreshTemplatesInput,
    RefreshTemplatesOutput,
    ReturnMachinesInput,
    ReturnMachinesOutput,
    StartMachinesInput,
    StartMachinesOutput,
    StopMachinesInput,
    StopMachinesOutput,
    SyncMachineInput,
    SyncMachineOutput,
    UpdateTemplateInput,
    UpdateTemplateOutput,
    ValidateTemplateInput,
    ValidateTemplateOutput,
)
from orb.application.services.orchestration.get_machine import GetMachineOrchestrator
from orb.application.services.orchestration.get_provider_config import (
    GetProviderConfigOrchestrator,
)
from orb.application.services.orchestration.get_provider_health import (
    GetProviderHealthOrchestrator,
)
from orb.application.services.orchestration.get_provider_metrics import (
    GetProviderMetricsOrchestrator,
)
from orb.application.services.orchestration.get_request_status import GetRequestStatusOrchestrator
from orb.application.services.orchestration.get_template import GetTemplateOrchestrator
from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
from orb.application.services.orchestration.list_providers import ListProvidersOrchestrator
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.application.services.orchestration.list_return_requests import (
    ListReturnRequestsOrchestrator,
)
from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
from orb.application.services.orchestration.refresh_templates import RefreshTemplatesOrchestrator
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.application.services.orchestration.start_machines import StartMachinesOrchestrator
from orb.application.services.orchestration.stop_machines import StopMachinesOrchestrator
from orb.application.services.orchestration.sync_machine import SyncMachineOrchestrator
from orb.application.services.orchestration.update_template import UpdateTemplateOrchestrator
from orb.application.services.orchestration.validate_template import ValidateTemplateOrchestrator
from orb.interface.response_formatting_service import ResponseFormattingService

In = TypeVar("In")
Out = TypeVar("Out")

Renderer = Callable[[ResponseFormattingService, Out], InterfaceResponse]


class Interface(Enum):
    """An interface through which a domain operation can be exposed."""

    CLI = "cli"
    REST = "rest"
    MCP = "mcp"
    SDK = "sdk"


@dataclass(frozen=True)
class CatalogEntry(Generic[In, Out]):
    """A single domain operation declared once for every interface.

    key                — stable operation name (e.g. ``list_machines``).
    orchestrator       — the orchestrator class that dispatches the operation.
    input_dto          — the input dataclass the orchestrator consumes.
    output_dto         — the output dataclass it returns.
    render             — default closure turning an output DTO into a rendered
                         body via the ResponseFormattingService.
    exposed_on         — interfaces on which the operation routes through the
                         orchestrator-and-formatter path today.
    render_overrides   — per-interface renderer where an interface's canonical
                         body legitimately differs (e.g. the CLI shows a machine
                         as an operation result while REST shows it as a detail).
    """

    key: str
    orchestrator: type[OrchestratorBase[In, Out]]
    input_dto: type[In]
    output_dto: type[Out]
    render: Renderer[Out]
    exposed_on: frozenset[Interface]
    render_overrides: dict[Interface, Renderer[Out]] = field(default_factory=dict)

    def renderer_for(self, iface: Interface) -> Renderer[Out]:
        """Return the interface-specific renderer, or the default when none."""
        return self.render_overrides.get(iface, self.render)


def bind_from_mapping(entry: CatalogEntry[Any, Any], data: Mapping[str, Any]) -> Any:
    """Construct an entry's input DTO from a flat mapping.

    Keys that do not name a field on the input DTO are dropped, so a dict-shaped
    source (an SDK keyword payload, a decoded JSON body) can be bound without the
    caller pre-filtering it. Field validation and defaulting remain the DTO's own
    responsibility.
    """
    dto_type = entry.input_dto
    field_names = {f.name for f in dataclasses.fields(dto_type)}
    return dto_type(**{k: v for k, v in data.items() if k in field_names})


_CLI = Interface.CLI
_REST = Interface.REST
_MCP = Interface.MCP
_SDK = Interface.SDK


def _render_request_status_cli(
    rfs: ResponseFormattingService, out: GetRequestStatusOutput
) -> InterfaceResponse:
    """Render request status for the CLI with an exit code that reflects the outcome.

    The exit code is the worst code across the requested IDs, as defined by the
    active scheduler: by default a request in a problem state (failed, cancelled,
    timeout, partial) or one that could not be looked up makes the command exit
    non-zero. The HostFactory scheduler always exits 0 and reports the outcome
    in the output.
    """
    response = rfs.format_request_status(out.requests)
    exit_code = 0
    for item in out.requests:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if status:
            code = rfs.status_poll_exit_code(str(status))
        elif item.get("error"):
            code = rfs.status_poll_exit_code(None)
        else:
            continue
        exit_code = max(exit_code, code)
    return InterfaceResponse(data=response.data, exit_code=exit_code)


def _render_machine_detail(
    rfs: ResponseFormattingService, out: GetMachineOutput
) -> InterfaceResponse:
    if out.machine is None:
        return rfs.format_error("Machine not found")
    return rfs.format_machine_detail(out.machine.to_dict())


def _render_machine_operation(
    rfs: ResponseFormattingService, out: GetMachineOutput
) -> InterfaceResponse:
    if out.machine is None:
        return rfs.format_error("Machine not found")
    return rfs.format_machine_operation(out.machine.to_dict())


def _render_sync_machine(
    rfs: ResponseFormattingService, out: SyncMachineOutput
) -> InterfaceResponse:
    if out.machine is None:
        return rfs.format_error("Machine not found")
    response = rfs.format_machine_detail(out.machine.to_dict())
    # Overlay the sync outcome onto the machine detail body: ``synced`` is always
    # present, and ``sync_error`` is added only when the provider refresh failed.
    body = {**response.data, "synced": out.synced}
    if out.error:
        body["sync_error"] = out.error
    return InterfaceResponse(data=body, exit_code=response.exit_code)


def _render_get_template(
    rfs: ResponseFormattingService, out: GetTemplateOutput
) -> InterfaceResponse:
    if out.template is None:
        return rfs.format_error("Template not found")
    return rfs.format_template_detail(out.template)


OPERATION_CATALOG: dict[str, CatalogEntry[Any, Any]] = {
    "request_machines": CatalogEntry(
        key="request_machines",
        orchestrator=AcquireMachinesOrchestrator,
        input_dto=AcquireMachinesInput,
        output_dto=AcquireMachinesOutput,
        render=lambda rfs, out: rfs.format_request_operation(
            {
                "request_id": out.request_id,
                "status": out.status,
                "machine_ids": out.machine_ids,
            },
            out.status,
        ),
        exposed_on=frozenset({_CLI, _REST, _MCP, _SDK}),
    ),
    "return_machines": CatalogEntry(
        key="return_machines",
        orchestrator=ReturnMachinesOrchestrator,
        input_dto=ReturnMachinesInput,
        output_dto=ReturnMachinesOutput,
        render=lambda rfs, out: rfs.format_request_operation(
            {
                "request_id": out.request_id,
                "status": out.status,
                "message": out.message,
                "skipped_machines": out.skipped_machines,
            },
            out.status,
        ),
        exposed_on=frozenset({_CLI, _REST, _MCP, _SDK}),
    ),
    "get_request_status": CatalogEntry(
        key="get_request_status",
        orchestrator=GetRequestStatusOrchestrator,
        input_dto=GetRequestStatusInput,
        output_dto=GetRequestStatusOutput,
        render=lambda rfs, out: rfs.format_request_status(out.requests),
        render_overrides={_CLI: _render_request_status_cli},
        exposed_on=frozenset({_CLI, _REST, _MCP, _SDK}),
    ),
    "list_requests": CatalogEntry(
        key="list_requests",
        orchestrator=ListRequestsOrchestrator,
        input_dto=ListRequestsInput,
        output_dto=ListRequestsOutput,
        render=lambda rfs, out: rfs.format_request_status(
            out.requests,
            total_count=out.total_count,
            next_cursor=out.next_cursor,
        ),
        exposed_on=frozenset({_CLI, _REST, _MCP, _SDK}),
    ),
    "list_return_requests": CatalogEntry(
        key="list_return_requests",
        orchestrator=ListReturnRequestsOrchestrator,
        input_dto=ListReturnRequestsInput,
        output_dto=ListReturnRequestsOutput,
        # REST and the SDK expose return requests in the request-status shape,
        # paginated like the other list operations. The CLI feeds IBM Symphony
        # HostFactory, which expects the dedicated getReturnRequests shape
        # (machine/grace-period items), so it renders through the return-requests
        # formatter. The MCP tool reuses the CLI handler, so it inherits that
        # shape through the CLI renderer.
        render=lambda rfs, out: rfs.format_request_status(
            out.requests,
            total_count=out.total_count,
            next_cursor=out.next_cursor,
        ),
        exposed_on=frozenset({_CLI, _REST, _MCP, _SDK}),
        render_overrides={
            _CLI: lambda rfs, out: rfs.format_return_requests(out.requests),
        },
    ),
    "cancel_request": CatalogEntry(
        key="cancel_request",
        orchestrator=CancelRequestOrchestrator,
        input_dto=CancelRequestInput,
        output_dto=CancelRequestOutput,
        render=lambda rfs, out: rfs.format_request_operation(
            {"request_id": out.request_id, "status": out.status},
            out.status,
        ),
        exposed_on=frozenset({_CLI, _REST, _MCP, _SDK}),
    ),
    "list_machines": CatalogEntry(
        key="list_machines",
        orchestrator=ListMachinesOrchestrator,
        input_dto=ListMachinesInput,
        output_dto=ListMachinesOutput,
        render=lambda rfs, out: rfs.format_machine_list(
            out.machines,
            total_count=out.total_count,
            next_cursor=out.next_cursor,
        ),
        exposed_on=frozenset({_CLI, _REST, _MCP, _SDK}),
    ),
    "get_machine": CatalogEntry(
        key="get_machine",
        orchestrator=GetMachineOrchestrator,
        input_dto=GetMachineInput,
        output_dto=GetMachineOutput,
        render=_render_machine_detail,
        exposed_on=frozenset({_CLI, _REST, _SDK, _MCP}),
        render_overrides={_CLI: _render_machine_operation},
    ),
    "sync_machine": CatalogEntry(
        key="sync_machine",
        orchestrator=SyncMachineOrchestrator,
        input_dto=SyncMachineInput,
        output_dto=SyncMachineOutput,
        render=_render_sync_machine,
        exposed_on=frozenset({_REST}),
    ),
    "stop_machines": CatalogEntry(
        key="stop_machines",
        orchestrator=StopMachinesOrchestrator,
        input_dto=StopMachinesInput,
        output_dto=StopMachinesOutput,
        render=lambda rfs, out: rfs.format_success(
            {
                "message": out.message,
                "stopped_machines": out.stopped_machines,
                "failed_machines": out.failed_machines,
            }
        ),
        exposed_on=frozenset({_CLI, _MCP}),
    ),
    "start_machines": CatalogEntry(
        key="start_machines",
        orchestrator=StartMachinesOrchestrator,
        input_dto=StartMachinesInput,
        output_dto=StartMachinesOutput,
        render=lambda rfs, out: rfs.format_success(
            {
                "message": out.message,
                "started_machines": out.started_machines,
                "failed_machines": out.failed_machines,
            }
        ),
        exposed_on=frozenset({_CLI, _MCP}),
    ),
    "list_templates": CatalogEntry(
        key="list_templates",
        orchestrator=ListTemplatesOrchestrator,
        input_dto=ListTemplatesInput,
        output_dto=ListTemplatesOutput,
        render=lambda rfs, out: rfs.format_template_list(
            out.templates,
            total_count=(out.total_count if out.total_count is not None else len(out.templates)),
            next_cursor=out.next_cursor,
        ),
        exposed_on=frozenset({_CLI, _REST, _MCP, _SDK}),
    ),
    "get_template": CatalogEntry(
        key="get_template",
        orchestrator=GetTemplateOrchestrator,
        input_dto=GetTemplateInput,
        output_dto=GetTemplateOutput,
        render=_render_get_template,
        exposed_on=frozenset({_CLI, _REST, _MCP, _SDK}),
    ),
    "create_template": CatalogEntry(
        key="create_template",
        orchestrator=CreateTemplateOrchestrator,
        input_dto=CreateTemplateInput,
        output_dto=CreateTemplateOutput,
        render=lambda rfs, out: rfs.format_template_mutation(
            {
                "template_id": out.template_id,
                "status": "created" if out.created else "validation_failed",
                "created": out.created,
                "validation_errors": out.validation_errors,
            }
        ),
        exposed_on=frozenset({_CLI, _REST, _SDK}),
    ),
    "update_template": CatalogEntry(
        key="update_template",
        orchestrator=UpdateTemplateOrchestrator,
        input_dto=UpdateTemplateInput,
        output_dto=UpdateTemplateOutput,
        render=lambda rfs, out: rfs.format_template_mutation(
            {
                "template_id": out.template_id,
                "status": "updated" if out.updated else "validation_failed",
                "updated": out.updated,
                "validation_errors": out.validation_errors,
            }
        ),
        exposed_on=frozenset({_CLI, _REST, _SDK}),
    ),
    "delete_template": CatalogEntry(
        key="delete_template",
        orchestrator=DeleteTemplateOrchestrator,
        input_dto=DeleteTemplateInput,
        output_dto=DeleteTemplateOutput,
        render=lambda rfs, out: rfs.format_template_mutation(
            {
                "template_id": out.template_id,
                "status": "deleted" if out.deleted else "not_found",
                "deleted": out.deleted,
            }
        ),
        exposed_on=frozenset({_CLI, _REST, _SDK}),
    ),
    "validate_template": CatalogEntry(
        key="validate_template",
        orchestrator=ValidateTemplateOrchestrator,
        input_dto=ValidateTemplateInput,
        output_dto=ValidateTemplateOutput,
        render=lambda rfs, out: rfs.format_template_mutation(
            {
                "template_id": out.template_id,
                "status": "validated",
                "valid": out.valid,
                "validation_errors": out.errors,
                "message": out.message,
            }
        ),
        exposed_on=frozenset({_CLI, _REST, _MCP, _SDK}),
    ),
    "refresh_templates": CatalogEntry(
        key="refresh_templates",
        orchestrator=RefreshTemplatesOrchestrator,
        input_dto=RefreshTemplatesInput,
        output_dto=RefreshTemplatesOutput,
        render=lambda rfs, out: rfs.format_template_list(out.templates),
        exposed_on=frozenset({_CLI, _REST, _SDK}),
    ),
    "get_provider_health": CatalogEntry(
        key="get_provider_health",
        orchestrator=GetProviderHealthOrchestrator,
        input_dto=GetProviderHealthInput,
        output_dto=GetProviderHealthOutput,
        render=lambda rfs, out: rfs.format_config({"health": out.health, "message": out.message}),
        exposed_on=frozenset({_CLI, _MCP}),
    ),
    "list_providers": CatalogEntry(
        key="list_providers",
        orchestrator=ListProvidersOrchestrator,
        input_dto=ListProvidersInput,
        output_dto=ListProvidersOutput,
        render=lambda rfs, out: rfs.format_config(
            {
                "providers": out.providers,
                "count": out.count,
                "selection_policy": out.selection_policy,
                "message": out.message,
            }
        ),
        exposed_on=frozenset({_CLI, _MCP}),
    ),
    "get_provider_config": CatalogEntry(
        key="get_provider_config",
        orchestrator=GetProviderConfigOrchestrator,
        input_dto=GetProviderConfigInput,
        output_dto=GetProviderConfigOutput,
        render=lambda rfs, out: rfs.format_config({"config": out.config, "message": out.message}),
        exposed_on=frozenset({_CLI, _MCP}),
    ),
    "get_provider_metrics": CatalogEntry(
        key="get_provider_metrics",
        orchestrator=GetProviderMetricsOrchestrator,
        input_dto=GetProviderMetricsInput,
        output_dto=GetProviderMetricsOutput,
        render=lambda rfs, out: rfs.format_config({"metrics": out.metrics, "message": out.message}),
        exposed_on=frozenset({_CLI, _MCP}),
    ),
}

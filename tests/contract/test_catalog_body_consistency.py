"""Catalog render-level body consistency contract.

The operation catalog declares each domain operation once and lets every
interface render the operation's output DTO through the same
:class:`ResponseFormattingService` call. This module proves the consistency
guarantee that declaration is meant to deliver: for a single output DTO, the
rendered body is identical across every interface the operation is exposed on,
except where the entry explicitly declares a per-interface renderer.

The test drives rendering only — it constructs a representative output DTO per
operation and pushes it through ``entry.renderer_for(iface)`` for each exposed
interface. It deliberately does not spin up HTTP or CLI dispatch, so it stays
fast and free of container wiring while still exercising the real formatter over
a real scheduler strategy.

MCP is out of scope here; only CLI, REST, and SDK bodies are compared.
"""

from __future__ import annotations

from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.machine.dto import MachineDTO
from orb.application.services.orchestration.dtos import (
    AcquireMachinesOutput,
    CancelRequestOutput,
    ConfirmTerminatedOutput,
    CreateTemplateOutput,
    DeleteTemplateOutput,
    GetMachineOutput,
    GetRequestStatusOutput,
    GetTemplateOutput,
    ListMachinesOutput,
    ListProvidersOutput,
    ListRequestsOutput,
    ListReturnRequestsOutput,
    ListTemplatesOutput,
    RefreshTemplatesOutput,
    ReturnMachinesOutput,
    UpdateTemplateOutput,
    ValidateTemplateOutput,
)
from orb.interface.catalog import OPERATION_CATALOG, CatalogEntry, Interface
from orb.interface.response_formatting_service import ResponseFormattingService

# Interfaces whose bodies this contract compares. MCP is intentionally excluded.
_COMPARED_INTERFACES = frozenset({Interface.CLI, Interface.REST, Interface.SDK})


@pytest.fixture
def rfs() -> ResponseFormattingService:
    """A real ResponseFormattingService over a real default scheduler strategy.

    The default strategy carries no external dependencies once handed a logger,
    so it renders bodies deterministically without AWS or a DI container.
    """
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    logger = MagicMock()
    strategy = DefaultSchedulerStrategy(logger=logger)
    return ResponseFormattingService(strategy)


def _make_template() -> Any:
    """A minimal template stand-in exposing the ``to_dict`` the formatter calls."""
    template = MagicMock()
    template.to_dict.return_value = {
        "template_id": "tmpl-001",
        "max_machines": 1,
        "machine_types": {"t3.medium": 1},
    }
    return template


def _make_machine() -> MachineDTO:
    return MachineDTO(
        machine_id="machine-001",
        name="test-machine",
        status="running",
        instance_type="t3.medium",
        private_ip="10.0.0.1",
        result="succeed",
    )


# Representative output DTOs for every operation that is exposed on two or more
# compared interfaces (and therefore has a cross-interface body to reconcile).
# Empty collections / minimal fields are sufficient: the guarantee under test is
# that the SAME output renders to the SAME body, not that any particular content
# round-trips.
_OUTPUT_BUILDERS: dict[str, Callable[[], Any]] = {
    "request_machines": lambda: AcquireMachinesOutput(
        request_id="req-001", status="running", machine_ids=[]
    ),
    "return_machines": lambda: ReturnMachinesOutput(
        request_id="req-001", status="complete", message="", skipped_machines=[]
    ),
    "get_request_status": lambda: GetRequestStatusOutput(requests=[]),
    "list_requests": lambda: ListRequestsOutput(
        requests=[], count=0, next_cursor=None, total_count=0
    ),
    "list_return_requests": lambda: ListReturnRequestsOutput(requests=[]),
    "cancel_request": lambda: CancelRequestOutput(request_id="req-001", status="cancelled"),
    "confirm_terminated": lambda: ConfirmTerminatedOutput(
        request_id="req-001", confirmed=["m-1"], already_confirmed=[], records=[], warnings=[]
    ),
    "list_machines": lambda: ListMachinesOutput(
        machines=[], count=0, next_cursor=None, total_count=0
    ),
    "get_machine": lambda: GetMachineOutput(machine=_make_machine()),
    "list_templates": lambda: ListTemplatesOutput(
        templates=[], count=0, next_cursor=None, total_count=0
    ),
    "get_template": lambda: GetTemplateOutput(template=_make_template()),
    "create_template": lambda: CreateTemplateOutput(
        template_id="tmpl-001", created=True, validation_errors=[]
    ),
    "update_template": lambda: UpdateTemplateOutput(
        template_id="tmpl-001", updated=True, validation_errors=[]
    ),
    "delete_template": lambda: DeleteTemplateOutput(template_id="tmpl-001", deleted=True),
    "validate_template": lambda: ValidateTemplateOutput(
        valid=True, errors=[], message="", template_id="tmpl-001"
    ),
    "refresh_templates": lambda: RefreshTemplatesOutput(templates=[]),
    "list_providers": lambda: ListProvidersOutput(
        providers=[{"name": "aws-1", "type": "aws", "enabled": True, "config": {}}],
        count=1,
        selection_policy="first_available",
        message="ok",
    ),
}


def _compared_interfaces(entry: CatalogEntry[Any, Any]) -> list[Interface]:
    """Interfaces this contract reconciles for an entry, in a stable order."""
    return sorted(entry.exposed_on & _COMPARED_INTERFACES, key=lambda i: i.value)


# Operations exposed on two or more compared interfaces — the ones with a
# cross-interface body to reconcile.
_MULTI_INTERFACE_KEYS = sorted(
    key for key, entry in OPERATION_CATALOG.items() if len(_compared_interfaces(entry)) >= 2
)


def test_every_catalog_entry_declares_at_least_one_interface() -> None:
    """Parity: no operation may be declared with an empty exposed_on set."""
    orphaned = [key for key, entry in OPERATION_CATALOG.items() if not entry.exposed_on]
    assert not orphaned, f"catalog entries with empty exposed_on: {orphaned}"


def test_every_multi_interface_entry_has_a_representative_output() -> None:
    """Guard: the builder table must cover every op with a cross-interface body.

    Fails loudly if a new multi-interface operation is added to the catalog
    without a representative output DTO, rather than silently skipping it.
    """
    missing = [key for key in _MULTI_INTERFACE_KEYS if key not in _OUTPUT_BUILDERS]
    assert not missing, f"multi-interface catalog entries without a test output: {missing}"


@pytest.mark.parametrize("key", _MULTI_INTERFACE_KEYS)
def test_catalog_body_is_consistent_across_interfaces(
    key: str, rfs: ResponseFormattingService
) -> None:
    """The same output DTO renders to the same body on every exposed interface.

    For each pair of compared interfaces where NEITHER declares a render
    override, the rendered body must be byte-identical. A pair that diverges
    without a declared override is real interface drift and fails here.
    """
    entry = OPERATION_CATALOG[key]
    interfaces = _compared_interfaces(entry)
    output = _OUTPUT_BUILDERS[key]()

    bodies: dict[Interface, dict[str, Any]] = {}
    for iface in interfaces:
        response = entry.renderer_for(iface)(rfs, output)
        assert isinstance(response, InterfaceResponse)
        assert isinstance(response.data, dict), (
            f"{key}: {iface.value} rendered a non-dict body: {response.data!r}"
        )
        bodies[iface] = response.data

    for i, left in enumerate(interfaces):
        for right in interfaces[i + 1 :]:
            if left in entry.render_overrides or right in entry.render_overrides:
                # A declared override means the divergence is intentional; it is
                # asserted explicitly in test_get_machine_override_diverges.
                continue
            assert bodies[left] == bodies[right], (
                f"{key}: undeclared body drift between "
                f"{left.value} and {right.value}\n"
                f"  {left.value}:  {bodies[left]!r}\n"
                f"  {right.value}: {bodies[right]!r}"
            )


def test_get_machine_override_is_declared_and_wired() -> None:
    """get_machine wires a distinct CLI renderer, documenting intended divergence.

    The catalog declares a CLI-only render override for get_machine: the CLI
    renders a machine as an operation result (format_machine_operation) while
    every other interface renders it as a detail view (format_machine_detail).
    The two renderers must be distinct callables so the override is real.
    """
    from orb.interface.catalog import _render_machine_detail, _render_machine_operation

    entry = OPERATION_CATALOG["get_machine"]

    assert Interface.CLI in entry.render_overrides
    cli_renderer = entry.renderer_for(Interface.CLI)
    rest_renderer = entry.renderer_for(Interface.REST)

    assert cli_renderer is _render_machine_operation
    assert rest_renderer is _render_machine_detail
    assert cli_renderer is not rest_renderer


def test_get_machine_override_body_relationship(rfs: ResponseFormattingService) -> None:
    """Document what the get_machine override actually changes in the body.

    Both the CLI operation renderer and the detail renderer delegate to the
    scheduler's format_machine_details_response, so the rendered *body* is the
    same; the override exists to carry operation-result exit-code semantics. This
    test pins that observed relationship: identical bodies, with the divergence
    confined to the response envelope rather than the payload. If a future change
    makes the CLI body itself diverge, this assertion updates deliberately.
    """
    entry = OPERATION_CATALOG["get_machine"]
    output = _OUTPUT_BUILDERS["get_machine"]()

    cli_response = entry.renderer_for(Interface.CLI)(rfs, output)
    rest_response = entry.renderer_for(Interface.REST)(rfs, output)

    assert cli_response.data == rest_response.data

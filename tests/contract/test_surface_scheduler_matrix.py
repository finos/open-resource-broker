"""Surface x format matrix for the operation catalog.

Every catalog operation exposed on more than one surface (CLI, REST, SDK, MCP)
is rendered for one representative output with each built-in response format.
Within a format the body must be the same on every surface, except where the
entry declares a per-surface renderer, and the formats must differ where the
scheduler has its own wire shape. The HostFactory rows run over the real
HostFactory strategy.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.orchestration.dtos import (
    AcquireMachinesOutput,
    ListReclaimedMachinesOutput,
    ListRequestsOutput,
)
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.scheduler.slurm.slurm_strategy import SlurmSchedulerStrategy
from orb.interface.catalog import OPERATION_CATALOG, Interface
from orb.interface.response_formatting_service import ResponseFormattingService
from tests.contract.test_catalog_body_consistency import _OUTPUT_BUILDERS

_SURFACES = frozenset(Interface)

# Operations that live on one surface only, with the reason they have no matrix row.
_SINGLE_SURFACE: dict[str, str] = {
    key: "exposed on a single surface"
    for key, entry in OPERATION_CATALOG.items()
    if len(entry.exposed_on) < 2
}
# ORB operations with no HostFactory counterpart: their body is the same in every format.
_SCHEDULER_NEUTRAL: dict[str, str] = {
    "get_provider_config": "ORB provider administration, no HostFactory operation",
    "get_provider_health": "ORB provider administration, no HostFactory operation",
    "get_provider_metrics": "ORB provider administration, no HostFactory operation",
    "start_machines": "ORB machine lifecycle, no HostFactory operation",
    "stop_machines": "ORB machine lifecycle, no HostFactory operation",
}
_MATRIX_KEYS = sorted(set(OPERATION_CATALOG) - set(_SINGLE_SURFACE) - set(_SCHEDULER_NEUTRAL))

_FORMATS = {
    "default": lambda: ResponseFormattingService(DefaultSchedulerStrategy(logger=MagicMock())),
    "hostfactory": lambda: ResponseFormattingService(HostFactorySchedulerStrategy()),
    "slurm": lambda: ResponseFormattingService(SlurmSchedulerStrategy()),
}


def _bodies(key: str, formatter: ResponseFormattingService) -> dict[Interface, Any]:
    entry = OPERATION_CATALOG[key]
    output = _OUTPUT_BUILDERS[key]()
    bodies = {}
    for surface in entry.exposed_on & _SURFACES:
        response = entry.renderer_for(surface)(formatter, output)
        assert isinstance(response, InterfaceResponse)
        bodies[surface] = response.data
    return bodies


def test_every_catalog_entry_has_a_matrix_row_or_a_reason() -> None:
    uncovered = [k for k in _MATRIX_KEYS if k not in _OUTPUT_BUILDERS]
    assert not uncovered, f"catalog entries without a representative output: {uncovered}"
    assert set(_SINGLE_SURFACE) | set(_SCHEDULER_NEUTRAL) | set(_MATRIX_KEYS) == set(
        OPERATION_CATALOG
    )
    assert set(_SCHEDULER_NEUTRAL) <= set(OPERATION_CATALOG)


@pytest.mark.parametrize("fmt", sorted(_FORMATS))
@pytest.mark.parametrize("key", _MATRIX_KEYS)
def test_one_body_per_format_across_surfaces(key: str, fmt: str) -> None:
    entry = OPERATION_CATALOG[key]
    bodies = _bodies(key, _FORMATS[fmt]())

    plain = [s for s in bodies if s not in entry.render_overrides]
    reference = bodies[plain[0]]
    for surface in plain[1:]:
        assert bodies[surface] == reference, f"{key}/{fmt}: {surface.value} drifts from the rest"


@pytest.mark.parametrize("key", ["get_request_status", "list_requests", "request_machines"])
def test_formats_differ_where_the_scheduler_has_its_own_shape(key: str) -> None:
    default_body = _bodies(key, _FORMATS["default"]())[Interface.REST]
    hf_body = _bodies(key, _FORMATS["hostfactory"]())[Interface.REST]

    assert default_body != hf_body


def test_hostfactory_request_creation_names_the_request_in_camel_case() -> None:
    output = AcquireMachinesOutput(request_id="req-001", status="running", machine_ids=[])
    entry = OPERATION_CATALOG["request_machines"]

    body = entry.renderer_for(Interface.REST)(_FORMATS["hostfactory"](), output).data

    assert body["requestId"] == "req-001"
    assert "request_id" not in body


def test_hostfactory_list_requests_has_no_snake_case_item_keys() -> None:
    output = ListRequestsOutput(
        requests=[
            {
                "request_id": "req-001",
                "status": "pending",
                "machines": [],
                "resource_ids": ["fleet-1"],
            }
        ],
        count=1,
        total_count=1,
    )

    body = OPERATION_CATALOG["list_requests"].renderer_for(Interface.REST)(
        _FORMATS["hostfactory"](), output
    )

    item = body.data["requests"][0]
    assert item["requestId"] == "req-001"
    assert not [k for k in item if "_" in k]


def test_reclaimed_machines_are_cli_only_and_follow_the_format() -> None:
    entry = OPERATION_CATALOG["list_reclaimed_machines"]
    output = ListReclaimedMachinesOutput(
        machines=[{"name": "host-1", "machine_id": "i-1", "grace_period": 120}]
    )

    assert entry.exposed_on == {Interface.CLI}
    hf = entry.renderer_for(Interface.CLI)(_FORMATS["hostfactory"](), output).data
    native = entry.renderer_for(Interface.CLI)(_FORMATS["default"](), output).data
    assert hf["requests"] == [{"machine": "host-1", "gracePeriod": 120}]
    assert native == {
        "reclaimed_machines": [{"name": "host-1", "machine_id": "i-1", "grace_period": 120}]
    }

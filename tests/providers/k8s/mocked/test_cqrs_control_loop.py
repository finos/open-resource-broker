"""CQRS control loop for the Kubernetes provider through the real buses.

Mirrors the AWS control-loop test: a ``CreateRequestCommand`` goes through the
real command bus, request-handler and provider registry to the Kubernetes
strategy (API emulated by kmock); a ``SyncAndGetRequestQuery`` reads it back and
the HostFactory formatter renders it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.providers.k8s.mocked.kmock_delivery_conftest import (
    _inject_kmock_factory,
    _make_k8s_logger,
    _register_deployment_resource,
    _register_job_resource,
    _register_pod_resource,
    _register_statefulset_resource,
)
from tests.shared.constants import REQUEST_ID_RE

pytestmark = pytest.mark.kmock

KINDS = {
    "Pod": "k8s-pod-example",
    "Deployment": "k8s-deployment-example",
    "StatefulSet": "k8s-statefulset-example",
    "Job": "k8s-job-example",
}

_BASELINE = json.loads(
    (Path(__file__).resolve().parents[3] / "fixtures" / "wire_format_baseline.json").read_text()
)
_WIRE_STATUSES = {v for k, v in _BASELINE.items() if k.startswith("request_status:")}


@pytest.fixture
def buses(orb_config_dir_k8s, kmock_k8s):
    from orb.application.ports.command_bus_port import CommandBusPort
    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.di.container import get_container

    for register in (
        _register_pod_resource,
        _register_deployment_resource,
        _register_statefulset_resource,
        _register_job_resource,
    ):
        register(kmock_k8s)

    container = get_container()
    _inject_kmock_factory(kmock_k8s, _make_k8s_logger())
    return (
        container.get(CommandBusPort),
        container.get(QueryBusPort),
        container.get(SchedulerPort),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(KINDS))
async def test_create_then_read_back_through_buses(buses, kmock_k8s, kind) -> None:
    from orb.application.dto.commands import CreateRequestCommand
    from orb.application.dto.queries import SyncAndGetRequestQuery

    command_bus, query_bus, scheduler = buses

    command = CreateRequestCommand(template_id=KINDS[kind], requested_count=1)
    await command_bus.execute(command)
    request_id = command.created_request_id
    assert request_id and REQUEST_ID_RE.match(request_id)

    plural = {"Pod": "pods", "Deployment": "deployments", "StatefulSet": "statefulsets"}.get(
        kind, "jobs"
    )
    assert plural in {key[0].plural for key in kmock_k8s.objects}

    request_dto = await query_bus.execute(SyncAndGetRequestQuery(request_id=request_id))
    assert request_dto is not None and request_dto.request_id == request_id

    entry = scheduler.format_request_status_response([request_dto])["requests"][0]
    assert entry["requestId"] == request_id
    assert entry["status"] in _WIRE_STATUSES
    assert entry["machines"], entry

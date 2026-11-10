"""Return status and release go through the real registry and handlers.

Only the Kubernetes client edge is faked (``FakeCoreV1``), so what the handlers
select and where they look is what is asserted: a return request labels no pod,
the pods carry the id of the request that created them, and they live in the
namespace recorded at acquire time.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.domain.base.operation_outcome import Accepted, Completed
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType
from orb.domain.request.value_objects import RequestId
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.shared.label_stamper import (
    stamp_native_workload_body,
)
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry
from tests.providers.k8s.unit.strategy.fake_core_v1 import FakeCoreV1, fake_client

NS = "orb-team-a"
ACQUIRE_ID = "req-11111111-1111-4111-8111-111111111111"

# Controller-owned pod names are what a Deployment/StatefulSet/Job really produces.
POD_NAMES = {
    "Pod": "orb-pod-0",
    "Deployment": "dep-7d9f-abcde",
    "StatefulSet": "sts-0",
    "Job": "job-x7k2p",
}
KINDS = list(POD_NAMES)


def _request(
    kind: str, request_id: str, request_type: RequestType, **provider_data: Any
) -> Request:
    return Request(
        request_id=RequestId(value=request_id),
        request_type=request_type,
        provider_type="k8s",
        provider_api=kind,
        template_id="tpl-1",
        requested_count=1,
        provider_data=provider_data,
    )


class Env:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.core = FakeCoreV1()
        self.registry = K8sHandlerRegistry(
            config=K8sProviderConfig(  # type: ignore[call-arg]
                namespace="default",
                max_retries=0,
                circuit_breaker_failure_threshold=1000,
            ),
            logger=MagicMock(),
            client_provider=lambda: fake_client(self.core),
            watch_manager_provider=lambda: None,
            plugin_factories=lambda: {},
            native_spec_service_provider=lambda: None,
        )
        self.return_id = f"ret-{uuid.uuid4()}"
        self.name = POD_NAMES[kind]

    def labels(self, request_id: str = ACQUIRE_ID) -> dict[str, str]:
        """Labels as the real spec builder of this kind stamps them on its pods."""
        acquire = _request(self.kind, request_id, RequestType.ACQUIRE)
        if self.kind == "Pod":
            handler = self.registry.get_handler("Pod")
            body = handler._stamp_native_pod_body(  # type: ignore[attr-defined]
                {}, pod_name=self.name, machine_id=self.name, namespace=NS, request=acquire
            )
            return dict(body["metadata"]["labels"])
        body = stamp_native_workload_body(
            {"spec": {"template": {}}},
            workload_name="wl",
            namespace=NS,
            replicas=1,
            request=acquire,
            label_prefix="orb.io",
        )
        return dict(body["spec"]["template"]["metadata"]["labels"])

    def add(self, name: Optional[str] = None, namespace: str = NS, **kw: Any) -> str:
        name = name or self.name
        self.core.add_pod(namespace, name, self.labels(kw.pop("request_id", ACQUIRE_ID)), **kw)
        return name

    def coords(
        self, names: list[str], namespace: str = NS, origin: Optional[str] = ACQUIRE_ID
    ) -> dict[str, dict[str, Any]]:
        return {
            n: {
                "provider_api": self.kind,
                "resource_id": "wl",
                "provider_data": {"namespace": namespace},
                "request_id": origin,
            }
            for n in names
        }

    def return_request(self, **provider_data: Any) -> Request:
        return _request(self.kind, self.return_id, RequestType.RETURN, **provider_data)

    async def status(self, names: list[str], coords: dict[str, Any], **provider_data: Any) -> Any:
        outcome = await self.registry.get_status(
            names, self.return_request(**provider_data), machine_coordinates=coords
        )
        # A return request labels no pod, so its id must never reach the apiserver.
        assert all(self.return_id not in " ".join(c) for c in self.core.calls)
        return outcome


@pytest.fixture(params=KINDS)
def env(request: pytest.FixtureRequest) -> Env:
    return Env(request.param)


@pytest.mark.asyncio
async def test_terminating_pod_keeps_return_pending(env: Env) -> None:
    # Phase, not deletionTimestamp, drives status: a terminating pod is still Running.
    name = env.add(terminating=True)
    outcome = await env.status([name], env.coords([name]))
    assert isinstance(outcome, Accepted)
    assert outcome.pending_resource_ids == [name]


@pytest.mark.asyncio
async def test_lingering_failed_pod_keeps_return_pending(env: Env) -> None:
    # "failed" is not terminal for a return: the pod object still exists.
    name = env.add(phase="Failed", ready=False)
    outcome = await env.status([name], env.coords([name]))
    assert isinstance(outcome, Accepted)
    assert outcome.pending_resource_ids == [name]


@pytest.mark.asyncio
async def test_absent_pod_completes_return(env: Env) -> None:
    outcome = await env.status([env.name], env.coords([env.name]))
    assert isinstance(outcome, Completed)
    assert outcome.resource_ids == [env.name]


@pytest.mark.asyncio
async def test_mixed_gone_and_running(env: Env) -> None:
    alive = env.add()
    gone = "gone-1"
    outcome = await env.status([alive, gone], env.coords([alive, gone]))
    assert isinstance(outcome, Accepted)
    assert outcome.pending_resource_ids == [alive]
    statuses = {i["instance_id"]: i["status"] for i in outcome.metadata["instances"]}
    assert statuses[gone] == "terminated"


@pytest.mark.asyncio
async def test_non_default_namespace_from_coordinates(env: Env) -> None:
    name = env.add()
    running = await env.status([name], env.coords([name]))
    assert isinstance(running, Accepted)
    del env.core.pods[(NS, name)]
    done = await env.status([name], env.coords([name]))
    assert isinstance(done, Completed)
    assert {c[1] for c in env.core.calls} == {NS}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ApiException(status=500),
        ApiException(status=404),
        TimeoutError("read timed out"),
    ],
    ids=["500", "404", "timeout"],
)
async def test_failed_list_is_unreadable_not_gone(env: Env, error: BaseException) -> None:
    env.core.list_errors[NS] = error
    outcome = await env.status([env.name], env.coords([env.name]))
    assert isinstance(outcome, Accepted)
    assert outcome.pending_resource_ids == [env.name]
    assert outcome.metadata["unreadable_resource_ids"] == [env.name]
    assert all(i["status"] != "terminated" for i in outcome.metadata["instances"])


@pytest.mark.asyncio
async def test_one_unreadable_namespace_does_not_complete_the_other(env: Env) -> None:
    other = f"{env.name}-b"
    env.core.list_errors["orb-team-b"] = ApiException(status=500)
    coords = {**env.coords([env.name]), **env.coords([other], namespace="orb-team-b")}
    outcome = await env.status([env.name, other], coords)
    assert isinstance(outcome, Accepted)
    assert outcome.pending_resource_ids == [other]
    statuses = {i["instance_id"]: i["status"] for i in outcome.metadata["instances"]}
    assert statuses == {env.name: "terminated"}


@pytest.mark.asyncio
async def test_machine_without_origin_id_falls_back_to_managed_label(env: Env) -> None:
    name = env.add()
    coords = env.coords([name], origin=None)
    running = await env.status([name], coords)
    assert isinstance(running, Accepted)
    assert ("list", NS, "orb.io/managed=true") in env.core.calls
    del env.core.pods[(NS, name)]
    assert isinstance(await env.status([name], coords), Completed)


@pytest.mark.asyncio
async def test_pod_of_another_request_is_not_the_returned_pod(env: Env) -> None:
    # Ordinal/name reuse by a later request must not keep the old return open.
    env.add(request_id="req-22222222-2222-4222-8222-222222222222")
    outcome = await env.status([env.name], env.coords([env.name]))
    assert isinstance(outcome, Completed)


@pytest.mark.asyncio
async def test_phantom_id_does_not_complete_return(env: Env) -> None:
    outcome = await env.status(["phantom-1"], env.coords(["phantom-1"]), pod_names=[])
    assert isinstance(outcome, Accepted)


@pytest.mark.asyncio
async def test_confirmed_pod_deletion_completes_return(env: Env) -> None:
    outcome = await env.status([env.name], env.coords([env.name]), pod_names=[env.name])
    assert isinstance(outcome, Completed)


@pytest.mark.asyncio
async def test_synthesised_machines_carry_the_kind_instance_type(env: Env) -> None:
    gone = await env.status([env.name], env.coords([env.name]), pod_names=[env.name])
    assert [i["instance_type"] for i in gone.metadata["instances"]] == [f"k8s/{env.kind}"]
    phantom = await env.status(["phantom-1"], env.coords(["phantom-1"]), pod_names=[])
    assert [(i["status"], i["instance_type"]) for i in phantom.metadata["instances"]] == [
        ("unknown", f"k8s/{env.kind}")
    ]

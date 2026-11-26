"""How long a return waits for pods the cluster could not be asked about.

The registry runs over a fake CoreV1 whose pod list fails; the clock is injected,
so no time function is patched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.domain.base.operation_outcome import Accepted
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType
from orb.domain.request.value_objects import RequestId
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry
from tests.providers.k8s.unit.strategy.fake_core_v1 import FakeCoreV1, fake_client

NS = "orb-team-a"
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
LIMIT = 600
COORDS = {
    "pod-a": {
        "provider_api": "Pod",
        "resource_id": None,
        "provider_data": {"namespace": NS},
        "request_id": "req-11111111-1111-4111-8111-111111111111",
    }
}


class FixedClock:
    def now(self) -> datetime:
        return NOW


def _registry(core: FakeCoreV1) -> K8sHandlerRegistry:
    return K8sHandlerRegistry(
        config=K8sProviderConfig(  # type: ignore[call-arg]
            namespace="default",
            max_retries=0,
            circuit_breaker_failure_threshold=1000,
            return_unreadable_timeout_seconds=LIMIT,
        ),
        logger=MagicMock(),
        client_provider=lambda: fake_client(core),
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
        clock=FixedClock(),  # type: ignore[arg-type]
    )


def _return(created_at: Optional[datetime]) -> Request:
    request = Request(
        request_id=RequestId(value="ret-22222222-2222-4222-8222-222222222222"),
        request_type=RequestType.RETURN,
        provider_type="k8s",
        provider_api="Pod",
        template_id="tpl-1",
        requested_count=1,
    )
    return request.model_copy(update={"created_at": created_at})


async def _status(created_at: Optional[datetime]) -> Any:
    core = FakeCoreV1()
    core.list_errors[NS] = ApiException(status=500)
    return await _registry(core).get_status(["pod-a"], _return(created_at), COORDS)


def _statuses(outcome: Any) -> list[str]:
    return [i["status"] for i in outcome.metadata["instances"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age", "expected"),
    [(LIMIT - 1, "unknown"), (LIMIT, "failed"), (LIMIT + 1, "failed")],
)
async def test_the_wait_ends_at_the_limit(age: int, expected: str) -> None:
    outcome = await _status(NOW - timedelta(seconds=age))

    assert isinstance(outcome, Accepted)
    assert _statuses(outcome) == [expected]
    assert outcome.metadata["unreadable_resource_ids"] == ["pod-a"]
    assert outcome.metadata.get("return_unreadable_timed_out", False) is (expected == "failed")


@pytest.mark.asyncio
async def test_a_creation_time_without_a_zone_is_utc() -> None:
    old = (NOW - timedelta(seconds=LIMIT + 1)).replace(tzinfo=None)
    young = (NOW - timedelta(seconds=LIMIT - 1)).replace(tzinfo=None)

    assert _statuses(await _status(old)) == ["failed"]
    assert _statuses(await _status(young)) == ["unknown"]


@pytest.mark.asyncio
async def test_a_request_without_a_creation_time_keeps_waiting() -> None:
    assert _statuses(await _status(None)) == ["unknown"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "pending", "starting", "shutting-down", "succeeded"])
async def test_a_readable_machine_still_leaving_defers_the_failure(
    status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = FakeCoreV1()
    registry = _registry(core)
    live = {"instance_id": "pod-b", "resource_id": "pod-b", "status": status}

    async def read(*_args: Any) -> Any:
        return [live], {"pod-a"}, MagicMock(state="in_progress")

    monkeypatch.setattr(registry, "_read_return_pods", read)
    coords = {**COORDS, "pod-b": COORDS["pod-a"]}
    old = NOW - timedelta(seconds=LIMIT + 1)

    waiting = await registry.get_status(["pod-a", "pod-b"], _return(old), coords)
    assert "failed" not in _statuses(waiting)
    assert "return_unreadable_timed_out" not in waiting.metadata

    live["status"] = "terminated"
    done = await registry.get_status(["pod-a", "pod-b"], _return(old), coords)
    assert sorted(_statuses(done)) == ["failed", "terminated"]
    assert done.metadata["return_unreadable_timed_out"] is True

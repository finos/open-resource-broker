"""The k8s internal ``starting`` status is translated when the result is built."""

from __future__ import annotations

from orb.domain.base.operation_outcome import Accepted
from orb.domain.machine.machine_status import MachineStatus
from orb.providers.k8s.strategy.outcome_bridge import _outcome_to_provider_result


def test_starting_pod_is_reported_as_pending_and_input_is_untouched() -> None:
    instances = [
        {"instance_id": "p-1", "status": "starting"},
        {"instance_id": "p-2", "status": "running"},
    ]
    outcome = Accepted(
        request_id="req-1",
        pending_resource_ids=["p-1", "p-2"],
        metadata={"instances": instances},
    )

    result = _outcome_to_provider_result(outcome, fallback_operation="get_instance_status")

    statuses = [inst["status"] for inst in result.data["instances"]]
    assert statuses == ["pending", "running"]
    for status in statuses:
        assert MachineStatus(status)
    assert instances[0]["status"] == "starting"

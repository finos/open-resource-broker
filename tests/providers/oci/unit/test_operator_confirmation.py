"""Operator-confirmed termination in the OCI status path and visibility probe."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from orb.domain.request.termination_confirmation import CONFIRMED_TERMINATED_KEY
from orb.providers.base.strategy import ProviderOperationType
from orb.providers.oci.cli_client import OCICommandError
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from tests.providers.oci.unit.test_command_execution import make_operation

NOT_FOUND = OCICommandError("missing", code="NotAuthorizedOrNotFound", status=404)


def _handler(run_oci):
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=MagicMock())
    handler._run_oci = MagicMock(side_effect=run_oci)
    handler._get_instance_network = MagicMock(return_value={})
    return handler


def _return_operation(machine_ids, confirmed):
    operation = make_operation(ProviderOperationType.GET_INSTANCE_STATUS)
    metadata = {CONFIRMED_TERMINATED_KEY: [{"machine_id": m} for m in confirmed]}
    operation.parameters["machine_ids"] = machine_ids
    operation.parameters["request"] = SimpleNamespace(
        request_type="return", requested_count=len(machine_ids), metadata=metadata
    )
    return operation


def _statuses(result):
    return {i["instance_id"]: i["status"] for i in result.data["instances"]}


def test_confirmed_machine_that_404s_is_reported_terminated():
    result = _handler(NOT_FOUND).get_instance_status(_return_operation(["i-1"], ["i-1"]))
    assert result.success
    assert _statuses(result) == {"i-1": "terminated"}
    assert result.metadata["provider_fulfilment"].state == "fulfilled"


def test_unconfirmed_sibling_still_fails_the_whole_call():
    result = _handler(NOT_FOUND).get_instance_status(_return_operation(["i-1", "i-2"], ["i-1"]))
    assert not result.success
    assert result.error_code == "OCI_STATUS_FAILED"


def test_running_instance_wins_over_a_stored_confirmation():
    def run(args, *a, **kw):
        return {"data": {"lifecycle-state": "RUNNING", "shape": "VM.Standard.E4.Flex"}}

    result = _handler(run).get_instance_status(_return_operation(["i-1"], ["i-1"]))
    assert result.success
    assert _statuses(result) == {"i-1": "running"}
    assert result.metadata["provider_fulfilment"].state == "in_progress"


def test_non_404_error_is_not_overridden_by_confirmation():
    handler = _handler(OCICommandError("boom", code="InternalError", status=500))
    result = handler.get_instance_status(_return_operation(["i-1"], ["i-1"]))
    assert not result.success


def test_confirmation_is_ignored_for_acquire_requests():
    operation = make_operation(ProviderOperationType.GET_INSTANCE_STATUS)
    operation.parameters["machine_ids"] = ["i-1"]
    operation.parameters["request"] = SimpleNamespace(
        request_type="acquire",
        requested_count=1,
        metadata={CONFIRMED_TERMINATED_KEY: [{"machine_id": "i-1"}]},
    )
    result = _handler(NOT_FOUND).get_instance_status(operation)
    assert result.data["instances"] == []


def _probe_operation(machine_ids):
    operation = make_operation(ProviderOperationType.GET_INSTANCE_STATUS)
    operation.parameters = {"instance_ids": machine_ids, "visibility_probe": True}
    return operation


def test_probe_reports_visible_instances_via_search_not_get_by_id():
    calls = []

    def run(args, *a, **kw):
        calls.append(args)
        return {"data": {"items": [{"identifier": "i-1", "lifecycle-state": "RUNNING"}]}}

    result = _handler(run).get_instance_status(_probe_operation(["i-1"]))
    assert result.success
    assert result.data["instances"][0]["instance_id"] == "i-1"
    assert result.data["instances"][0]["status"] == "visible"
    assert calls[0][:3] == ["search", "resource", "structured-search"]


def test_probe_with_no_hits_reports_instances_gone():
    result = _handler(lambda *a, **k: {"data": {"items": []}}).get_instance_status(
        _probe_operation(["i-1"])
    )
    assert result.success
    assert [(i["instance_id"], i["status"]) for i in result.data["instances"]] == [("i-1", "gone")]


def test_probe_reports_terminated_search_hit_as_terminated():
    def run(*a, **k):
        return {"data": {"items": [{"identifier": "i-1", "lifecycle-state": "TERMINATED"}]}}

    result = _handler(run).get_instance_status(_probe_operation(["i-1"]))
    assert result.data["instances"][0]["status"] == "terminated"


def test_probe_failure_is_reported_unavailable():
    result = _handler(NOT_FOUND).get_instance_status(_probe_operation(["i-1"]))
    assert not result.success
    assert result.error_code == "OCI_PROBE_UNAVAILABLE"


def test_probe_rejects_ids_that_could_alter_the_query():
    run = MagicMock()
    result = _handler(run).get_instance_status(_probe_operation(["i-1' or 1=1 --"]))
    assert not result.success
    run.assert_not_called()


def test_probe_batches_ids_into_few_searches():
    calls = []

    def run(args, *a, **kw):
        calls.append(args)
        return {"data": {"items": []}}

    ids = [f"i-{n}" for n in range(25)]
    result = _handler(run).get_instance_status(_probe_operation(ids))
    assert result.success
    assert len(calls) == 3
    assert all("||" in c[-1] for c in calls[:2])
    assert "identifier = 'i-24'" in calls[2][-1]


def test_probe_reports_hits_from_any_batch():
    def run(args, *a, **kw):
        if "identifier = 'i-15'" in args[-1]:
            return {"data": {"items": [{"identifier": "i-15", "lifecycle-state": "RUNNING"}]}}
        return {"data": {"items": []}}

    ids = [f"i-{n}" for n in range(20)]
    result = _handler(run).get_instance_status(_probe_operation(ids))
    states = {i["instance_id"]: i["status"] for i in result.data["instances"]}
    assert states["i-15"] == "visible"
    assert {s for m, s in states.items() if m != "i-15"} == {"gone"}


def test_probe_over_time_budget_is_unavailable(monkeypatch):
    from orb.providers.oci.handlers import oci_compute_handler as module

    clock = iter([0.0, 0.0, 100.0, 100.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    ids = [f"i-{n}" for n in range(25)]
    run = MagicMock(return_value={"data": {"items": []}})
    result = _handler(run).get_instance_status(_probe_operation(ids))
    assert not result.success
    assert result.error_code == "OCI_PROBE_UNAVAILABLE"

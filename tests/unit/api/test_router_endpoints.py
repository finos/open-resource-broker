"""Router endpoint tests for machines and requests routers."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import (
    get_acquire_machines_orchestrator,
    get_cancel_request_orchestrator,
    get_list_machines_orchestrator,
    get_list_requests_orchestrator,
    get_list_return_requests_orchestrator,
    get_machine_orchestrator,
    get_request_status_orchestrator,
    get_return_machines_orchestrator,
    get_scheduler_strategy,
)
from orb.api.routers.machines import router as machines_router
from orb.api.routers.requests import router as requests_router
from orb.application.services.orchestration.dtos import (
    AcquireMachinesInput,
    AcquireMachinesOutput,
    CancelRequestInput,
    CancelRequestOutput,
    GetMachineInput,
    GetMachineOutput,
    GetRequestStatusInput,
    GetRequestStatusOutput,
    ListMachinesInput,
    ListMachinesOutput,
    ListRequestsInput,
    ListRequestsOutput,
    ListReturnRequestsInput,
    ListReturnRequestsOutput,
    ReturnMachinesInput,
    ReturnMachinesOutput,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def machines_app():
    app = FastAPI()
    app.include_router(machines_router)
    return app


@pytest.fixture()
def requests_app():
    app = FastAPI()
    app.include_router(requests_router)
    return app


# ---------------------------------------------------------------------------
# Machines Router Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestMachinesRouter:
    """Tests for the /machines router."""

    def _make_scheduler(self):
        scheduler = MagicMock()
        scheduler.format_request_response.return_value = {"request_id": "req-abc", "message": "ok"}
        scheduler.format_machine_status_response.return_value = {"machines": []}
        scheduler.format_machine_details_response.return_value = {"machine_id": "i-123"}
        return scheduler

    def _override_acquire(self, app, output: AcquireMachinesOutput):
        orch = MagicMock()
        orch.execute = AsyncMock(return_value=output)
        app.dependency_overrides[get_acquire_machines_orchestrator] = lambda: orch
        return orch

    def _override_return(self, app, output: ReturnMachinesOutput):
        orch = MagicMock()
        orch.execute = AsyncMock(return_value=output)
        app.dependency_overrides[get_return_machines_orchestrator] = lambda: orch
        return orch

    def _override_list_machines(self, app, output: ListMachinesOutput):
        orch = MagicMock()
        orch.execute = AsyncMock(return_value=output)
        app.dependency_overrides[get_list_machines_orchestrator] = lambda: orch
        return orch

    def _override_get_machine(self, app, output: GetMachineOutput):
        orch = MagicMock()
        orch.execute = AsyncMock(return_value=output)
        app.dependency_overrides[get_machine_orchestrator] = lambda: orch
        return orch

    def _set_scheduler(self, app, scheduler=None):
        s = scheduler or self._make_scheduler()
        app.dependency_overrides[get_scheduler_strategy] = lambda: s
        return s

    def test_request_machines_happy_path(self, machines_app):
        output = AcquireMachinesOutput(
            request_id="req-abc", status="pending", raw={"request_id": "req-abc", "message": "ok"}
        )
        orch = self._override_acquire(machines_app, output)
        scheduler = self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.post("/machines/request", json={"template_id": "t1", "count": 3})

        assert resp.status_code == 202
        orch.execute.assert_awaited_once()
        inp: AcquireMachinesInput = orch.execute.call_args.args[0]
        assert inp.template_id == "t1"
        assert inp.requested_count == 3
        scheduler.format_request_response.assert_called_once_with(output.raw)

    def test_request_machines_camel_case_body(self, machines_app):
        output = AcquireMachinesOutput(request_id="req-camel", status="pending", raw={})
        orch = self._override_acquire(machines_app, output)
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.post("/machines/request", json={"templateId": "t1", "machineCount": 3})

        assert resp.status_code == 202
        inp: AcquireMachinesInput = orch.execute.call_args.args[0]
        assert inp.template_id == "t1"
        assert inp.requested_count == 3

    def test_request_machines_snake_case_count_alias(self, machines_app):
        output = AcquireMachinesOutput(request_id="req-snake", status="pending", raw={})
        orch = self._override_acquire(machines_app, output)
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.post("/machines/request", json={"template_id": "t1", "machine_count": 5})

        assert resp.status_code == 202
        inp: AcquireMachinesInput = orch.execute.call_args.args[0]
        assert inp.requested_count == 5

    def test_request_machines_missing_template_id(self, machines_app):
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.post("/machines/request", json={"count": 3})

        assert resp.status_code == 422

    def test_request_machines_missing_count(self, machines_app):
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.post("/machines/request", json={"template_id": "t1"})

        assert resp.status_code == 422

    def test_return_machines_happy_path(self, machines_app):
        output = ReturnMachinesOutput(
            request_id="ret-1", status="pending", raw={"returned": ["i-123"]}
        )
        orch = self._override_return(machines_app, output)
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.post("/machines/return", json={"machine_ids": ["i-123"]})

        assert resp.status_code == 200
        orch.execute.assert_awaited_once()
        inp: ReturnMachinesInput = orch.execute.call_args.args[0]
        assert inp.machine_ids == ["i-123"]

    def test_return_machines_empty_ids(self, machines_app):
        output = ReturnMachinesOutput(request_id=None, status="pending", raw={})
        orch = self._override_return(machines_app, output)
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.post("/machines/return", json={"machine_ids": []})

        assert resp.status_code == 200
        orch.execute.assert_awaited_once()

    def test_return_machines_camel_case_body(self, machines_app):
        output = ReturnMachinesOutput(request_id=None, status="pending", raw={})
        orch = self._override_return(machines_app, output)
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.post("/machines/return", json={"machineIds": ["i-456"]})

        assert resp.status_code == 200
        inp: ReturnMachinesInput = orch.execute.call_args.args[0]
        assert inp.machine_ids == ["i-456"]

    def test_list_machines_happy_path(self, machines_app):
        output = ListMachinesOutput(machines=[])
        orch = self._override_list_machines(machines_app, output)
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.get("/machines/")

        assert resp.status_code == 200
        orch.execute.assert_awaited_once()
        inp: ListMachinesInput = orch.execute.call_args.args[0]
        assert isinstance(inp, ListMachinesInput)

    def test_list_machines_with_filters(self, machines_app):
        output = ListMachinesOutput(machines=[])
        orch = self._override_list_machines(machines_app, output)
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.get("/machines/?status=running&limit=10")

        assert resp.status_code == 200
        inp: ListMachinesInput = orch.execute.call_args.args[0]
        assert inp.status == "running"
        assert inp.limit == 10

    def test_list_machines_with_request_id_filter(self, machines_app):
        output = ListMachinesOutput(machines=[])
        orch = self._override_list_machines(machines_app, output)
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.get("/machines/?request_id=req-xyz")

        assert resp.status_code == 200
        inp: ListMachinesInput = orch.execute.call_args.args[0]
        assert inp.request_id == "req-xyz"

    def test_get_machine_happy_path(self, machines_app):
        machine = MagicMock()
        machine.to_dict.return_value = {"machine_id": "i-123", "status": "running"}
        output = GetMachineOutput(machine=machine)
        orch = self._override_get_machine(machines_app, output)
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.get("/machines/i-123")

        assert resp.status_code == 200
        orch.execute.assert_awaited_once()
        inp: GetMachineInput = orch.execute.call_args.args[0]
        assert inp.machine_id == "i-123"

    def test_get_machine_not_found(self, machines_app):
        output = GetMachineOutput(machine=None)
        self._override_get_machine(machines_app, output)
        self._set_scheduler(machines_app)
        client = TestClient(machines_app, raise_server_exceptions=False)

        resp = client.get("/machines/i-missing")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Requests Router Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestRequestsRouter:
    """Tests for the /requests router."""

    def _make_scheduler(self):
        scheduler = MagicMock()
        scheduler.format_request_status_response.return_value = {"requests": []}
        scheduler.format_request_response.return_value = {
            "request_id": "req-789",
            "status": "cancelled",
        }
        return scheduler

    def _set_scheduler(self, app, scheduler=None):
        s = scheduler or self._make_scheduler()
        app.dependency_overrides[get_scheduler_strategy] = lambda: s
        return s

    def _override_list_requests(self, app, output: ListRequestsOutput):
        orch = MagicMock()
        orch.execute = AsyncMock(return_value=output)
        app.dependency_overrides[get_list_requests_orchestrator] = lambda: orch
        return orch

    def _override_list_return_requests(self, app, output: ListReturnRequestsOutput):
        orch = MagicMock()
        orch.execute = AsyncMock(return_value=output)
        app.dependency_overrides[get_list_return_requests_orchestrator] = lambda: orch
        return orch

    def _override_status(self, app, output: GetRequestStatusOutput):
        orch = MagicMock()
        orch.execute = AsyncMock(return_value=output)
        app.dependency_overrides[get_request_status_orchestrator] = lambda: orch
        return orch

    def _override_cancel(self, app, output: CancelRequestOutput):
        orch = MagicMock()
        orch.execute = AsyncMock(return_value=output)
        app.dependency_overrides[get_cancel_request_orchestrator] = lambda: orch
        return orch

    def test_list_requests_happy_path(self, requests_app):
        output = ListRequestsOutput(requests=[{"request_id": "req-1", "status": "pending"}])
        orch = self._override_list_requests(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        resp = client.get("/requests/")

        assert resp.status_code == 200
        orch.execute.assert_awaited_once()
        inp: ListRequestsInput = orch.execute.call_args.args[0]
        assert isinstance(inp, ListRequestsInput)
        assert inp.sync is False

    def test_list_requests_with_status_filter(self, requests_app):
        output = ListRequestsOutput(requests=[])
        orch = self._override_list_requests(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        resp = client.get("/requests/?status=pending")

        assert resp.status_code == 200
        inp: ListRequestsInput = orch.execute.call_args.args[0]
        assert inp.status == "pending"

    def test_list_requests_with_limit(self, requests_app):
        output = ListRequestsOutput(requests=[])
        orch = self._override_list_requests(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        resp = client.get("/requests/?limit=5")

        assert resp.status_code == 200
        inp: ListRequestsInput = orch.execute.call_args.args[0]
        assert inp.limit == 5

    def test_list_requests_with_sync(self, requests_app):
        output = ListRequestsOutput(requests=[])
        orch = self._override_list_requests(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        resp = client.get("/requests/?sync=true")

        assert resp.status_code == 200
        inp: ListRequestsInput = orch.execute.call_args.args[0]
        assert inp.sync is True

    def test_list_return_requests(self, requests_app):
        output = ListReturnRequestsOutput(requests=[{"request_id": "ret-1"}])
        orch = self._override_list_return_requests(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        resp = client.get("/requests/return")

        assert resp.status_code == 200
        orch.execute.assert_awaited_once()
        inp: ListReturnRequestsInput = orch.execute.call_args.args[0]
        assert isinstance(inp, ListReturnRequestsInput)

    def test_list_return_requests_with_limit(self, requests_app):
        output = ListReturnRequestsOutput(requests=[])
        orch = self._override_list_return_requests(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        resp = client.get("/requests/return?limit=20")

        assert resp.status_code == 200
        inp: ListReturnRequestsInput = orch.execute.call_args.args[0]
        assert inp.limit == 20

    def test_get_request_status(self, requests_app):
        output = GetRequestStatusOutput(requests=[{"request_id": "req-123", "status": "running"}])
        orch = self._override_status(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        resp = client.get("/requests/req-123/status")

        assert resp.status_code == 200
        orch.execute.assert_awaited_once()
        inp: GetRequestStatusInput = orch.execute.call_args.args[0]
        assert inp.request_ids == ["req-123"]
        assert inp.all_requests is False

    def test_get_request_status_long_default_true(self, requests_app):
        output = GetRequestStatusOutput(requests=[])
        orch = self._override_status(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        client.get("/requests/req-123/status")

        inp: GetRequestStatusInput = orch.execute.call_args.args[0]
        assert inp.detailed is True

    def test_get_request_status_long_false(self, requests_app):
        output = GetRequestStatusOutput(requests=[])
        orch = self._override_status(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        client.get("/requests/req-123/status?long=false")

        inp: GetRequestStatusInput = orch.execute.call_args.args[0]
        assert inp.detailed is False

    def test_get_request_details(self, requests_app):
        # GET /requests/{id} (no /status) was removed; expect 404 or 405
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        resp = client.get("/requests/req-456")

        assert resp.status_code in (404, 405)

    def test_cancel_request_happy_path(self, requests_app):
        output = CancelRequestOutput(
            request_id="req-789",
            status="cancelled",
            raw={"request_id": "req-789", "status": "cancelled"},
        )
        orch = self._override_cancel(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        resp = client.delete("/requests/req-789")

        assert resp.status_code == 200
        orch.execute.assert_awaited_once()
        inp: CancelRequestInput = orch.execute.call_args.args[0]
        assert inp.request_id == "req-789"
        assert inp.reason == "Cancelled via REST API"

    def test_cancel_request_with_reason(self, requests_app):
        output = CancelRequestOutput(request_id="req-789", status="cancelled", raw={})
        orch = self._override_cancel(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        resp = client.delete("/requests/req-789?reason=no+longer+needed")

        assert resp.status_code == 200
        inp: CancelRequestInput = orch.execute.call_args.args[0]
        assert inp.reason == "no longer needed"

    def test_cancel_request_default_reason(self, requests_app):
        output = CancelRequestOutput(request_id="req-999", status="cancelled", raw={})
        orch = self._override_cancel(requests_app, output)
        self._set_scheduler(requests_app)
        client = TestClient(requests_app, raise_server_exceptions=False)

        client.delete("/requests/req-999")

        inp: CancelRequestInput = orch.execute.call_args.args[0]
        assert inp.reason == "Cancelled via REST API"

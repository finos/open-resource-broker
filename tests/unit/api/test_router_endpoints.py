"""Router endpoint tests for machines and requests routers."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import (
    get_command_bus,
    get_query_bus,
    get_request_machines_handler,
    get_request_status_handler,
    get_return_machines_handler,
    get_scheduler_strategy,
)
from orb.api.routers.machines import router as machines_router
from orb.api.routers.requests import router as requests_router
from orb.application.dto.commands import CancelRequestCommand
from orb.application.dto.queries import (
    GetMachineQuery,
    ListActiveRequestsQuery,
    ListMachinesQuery,
    ListReturnRequestsQuery,
)
from orb.application.request.queries import ListRequestsQuery

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

    def _make_client(
        self, app, mock_query_bus=None, mock_request_handler=None, mock_return_handler=None
    ):
        mock_scheduler = MagicMock()
        mock_scheduler.format_machine_status_response.return_value = {"machines": []}
        mock_scheduler.format_machine_details_response.return_value = {}
        app.dependency_overrides[get_scheduler_strategy] = lambda: mock_scheduler
        if mock_query_bus is not None:
            app.dependency_overrides[get_query_bus] = lambda: mock_query_bus
        if mock_request_handler is not None:
            app.dependency_overrides[get_request_machines_handler] = lambda: mock_request_handler
        if mock_return_handler is not None:
            app.dependency_overrides[get_return_machines_handler] = lambda: mock_return_handler
        return TestClient(app, raise_server_exceptions=False)

    def _make_request_handler(self, request_id="req-abc"):
        handler = MagicMock()
        handler.handle = AsyncMock(return_value={"request_id": request_id, "message": "ok"})
        return handler

    def test_request_machines_happy_path(self, machines_app):
        handler = self._make_request_handler("req-abc")
        client = self._make_client(machines_app, mock_request_handler=handler)

        resp = client.post("/machines/request", json={"template_id": "t1", "count": 3})

        assert resp.status_code == 202
        body = resp.json()
        assert body["request_id"] == "req-abc"
        assert body["message"] == "ok"
        handler.handle.assert_awaited_once()

    def test_request_machines_camel_case_body(self, machines_app):
        handler = self._make_request_handler("req-camel")
        client = self._make_client(machines_app, mock_request_handler=handler)

        resp = client.post("/machines/request", json={"templateId": "t1", "machineCount": 3})

        assert resp.status_code == 202
        assert resp.json()["request_id"] == "req-camel"

    def test_request_machines_snake_case_count_alias(self, machines_app):
        handler = self._make_request_handler("req-snake")
        client = self._make_client(machines_app, mock_request_handler=handler)

        resp = client.post("/machines/request", json={"template_id": "t1", "machine_count": 5})

        assert resp.status_code == 202
        assert resp.json()["request_id"] == "req-snake"

    def test_request_machines_missing_template_id(self, machines_app):
        handler = self._make_request_handler()
        client = self._make_client(machines_app, mock_request_handler=handler)

        resp = client.post("/machines/request", json={"count": 3})

        assert resp.status_code == 422

    def test_request_machines_missing_count(self, machines_app):
        handler = self._make_request_handler()
        client = self._make_client(machines_app, mock_request_handler=handler)

        resp = client.post("/machines/request", json={"template_id": "t1"})

        assert resp.status_code == 422

    def test_return_machines_happy_path(self, machines_app):
        handler = MagicMock()
        handler.handle = AsyncMock(return_value={"returned": ["i-123"]})
        client = self._make_client(machines_app, mock_return_handler=handler)

        resp = client.post("/machines/return", json={"machine_ids": ["i-123"]})

        assert resp.status_code == 200
        handler.handle.assert_awaited_once()
        call_arg = handler.handle.call_args.args[0]
        assert call_arg["input_data"]["machine_ids"] == ["i-123"]

    def test_return_machines_empty_ids(self, machines_app):
        handler = MagicMock()
        handler.handle = AsyncMock(return_value={})
        client = self._make_client(machines_app, mock_return_handler=handler)

        resp = client.post("/machines/return", json={"machine_ids": []})

        assert resp.status_code == 200
        handler.handle.assert_awaited_once()

    def test_return_machines_camel_case_body(self, machines_app):
        handler = MagicMock()
        handler.handle = AsyncMock(return_value={})
        client = self._make_client(machines_app, mock_return_handler=handler)

        resp = client.post("/machines/return", json={"machineIds": ["i-456"]})

        assert resp.status_code == 200
        call_arg = handler.handle.call_args.args[0]
        assert call_arg["input_data"]["machine_ids"] == ["i-456"]

    def test_list_machines_happy_path(self, machines_app):
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=[])
        client = self._make_client(machines_app, mock_query_bus=query_bus)

        resp = client.get("/machines/")

        assert resp.status_code == 200
        query_bus.execute.assert_awaited_once()
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, ListMachinesQuery)

    def test_list_machines_with_filters(self, machines_app):
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=[])
        client = self._make_client(machines_app, mock_query_bus=query_bus)

        resp = client.get("/machines/?status=running&limit=10")

        assert resp.status_code == 200
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, ListMachinesQuery)
        assert query.status == "running"
        assert query.limit == 10

    def test_list_machines_with_request_id_filter(self, machines_app):
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=[])
        client = self._make_client(machines_app, mock_query_bus=query_bus)

        resp = client.get("/machines/?request_id=req-xyz")

        assert resp.status_code == 200
        query = query_bus.execute.call_args.args[0]
        assert query.request_id == "req-xyz"

    def test_get_machine_happy_path(self, machines_app):
        query_bus = AsyncMock()
        result = MagicMock()
        result.to_dict.return_value = {"machine_id": "i-123", "status": "running"}
        query_bus.execute = AsyncMock(return_value=result)
        client = self._make_client(machines_app, mock_query_bus=query_bus)

        resp = client.get("/machines/i-123")

        assert resp.status_code == 200
        query_bus.execute.assert_awaited_once()
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, GetMachineQuery)
        assert query.machine_id == "i-123"

    def test_request_machines_passes_additional_data(self, machines_app):
        handler = self._make_request_handler("req-extra")
        client = self._make_client(machines_app, mock_request_handler=handler)

        resp = client.post(
            "/machines/request",
            json={"template_id": "t1", "count": 2, "additional_data": {"region": "us-east-1"}},
        )

        assert resp.status_code == 202
        call_arg = handler.handle.call_args.args[0]
        # additional_data is merged into the template payload
        assert call_arg.template.get("region") == "us-east-1"


# ---------------------------------------------------------------------------
# Requests Router Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestRequestsRouter:
    """Tests for the /requests router."""

    def _make_client(
        self, app, mock_query_bus=None, mock_command_bus=None, mock_status_handler=None
    ):
        mock_scheduler = MagicMock()
        mock_scheduler.format_request_status_response.return_value = {"requests": []}
        mock_scheduler.format_request_response.return_value = {
            "request_id": "req-789",
            "status": "cancelled",
        }
        app.dependency_overrides[get_scheduler_strategy] = lambda: mock_scheduler
        if mock_query_bus is not None:
            app.dependency_overrides[get_query_bus] = lambda: mock_query_bus
        if mock_command_bus is not None:
            app.dependency_overrides[get_command_bus] = lambda: mock_command_bus
        if mock_status_handler is not None:
            app.dependency_overrides[get_request_status_handler] = lambda: mock_status_handler
        return TestClient(app, raise_server_exceptions=False)

    def _make_list_result(self, request_id="req-1", status="pending"):
        item = MagicMock()
        item.model_dump.return_value = {"request_id": request_id, "status": status}
        return [item]

    def _make_sync_result(self, request_id="req-1", status="running"):
        item = MagicMock()
        item.to_dict.return_value = {"request_id": request_id, "status": status}
        return [item]

    def test_list_requests_happy_path(self, requests_app):
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=self._make_list_result())
        client = self._make_client(requests_app, mock_query_bus=query_bus)

        resp = client.get("/requests/")

        assert resp.status_code == 200
        query_bus.execute.assert_awaited_once()
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, ListRequestsQuery)

    def test_list_requests_with_status_filter(self, requests_app):
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=self._make_list_result(status="pending"))
        client = self._make_client(requests_app, mock_query_bus=query_bus)

        resp = client.get("/requests/?status=pending")

        assert resp.status_code == 200
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, ListRequestsQuery)
        assert query.status == "pending"

    def test_list_requests_with_limit(self, requests_app):
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=self._make_list_result())
        client = self._make_client(requests_app, mock_query_bus=query_bus)

        resp = client.get("/requests/?limit=5")

        assert resp.status_code == 200
        query = query_bus.execute.call_args.args[0]
        assert query.limit == 5

    def test_list_requests_with_sync(self, requests_app):
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=self._make_sync_result())
        client = self._make_client(requests_app, mock_query_bus=query_bus)

        resp = client.get("/requests/?sync=true")

        assert resp.status_code == 200
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, ListActiveRequestsQuery)
        assert query.all_resources is True

    def test_list_return_requests(self, requests_app):
        query_bus = AsyncMock()
        item = MagicMock()
        item.to_dict.return_value = {"request_id": "ret-1", "status": "pending_return"}
        query_bus.execute = AsyncMock(return_value=[item])
        client = self._make_client(requests_app, mock_query_bus=query_bus)

        resp = client.get("/requests/return")

        assert resp.status_code == 200
        query_bus.execute.assert_awaited_once()
        query = query_bus.execute.call_args.args[0]
        assert isinstance(query, ListReturnRequestsQuery)

    def test_list_return_requests_with_limit(self, requests_app):
        query_bus = AsyncMock()
        query_bus.execute = AsyncMock(return_value=[])
        client = self._make_client(requests_app, mock_query_bus=query_bus)

        resp = client.get("/requests/return?limit=20")

        assert resp.status_code == 200
        query = query_bus.execute.call_args.args[0]
        assert query.limit == 20

    def test_get_request_status(self, requests_app):
        handler = MagicMock()
        handler.handle = AsyncMock(
            return_value={"requests": [{"request_id": "req-123", "status": "running"}]}
        )
        client = self._make_client(requests_app, mock_status_handler=handler)

        resp = client.get("/requests/req-123/status")

        assert resp.status_code == 200
        handler.handle.assert_awaited_once()
        call_arg = handler.handle.call_args.args[0]
        assert call_arg["input_data"]["requests"][0]["requestId"] == "req-123"
        assert call_arg["all_flag"] is False

    def test_get_request_status_long_default_true(self, requests_app):
        handler = MagicMock()
        handler.handle = AsyncMock(return_value=MagicMock())
        client = self._make_client(requests_app, mock_status_handler=handler)

        client.get("/requests/req-123/status")

        call_arg = handler.handle.call_args.args[0]
        # long defaults to True in the router
        assert call_arg["long"] is True

    def test_get_request_status_long_false(self, requests_app):
        handler = MagicMock()
        handler.handle = AsyncMock(return_value=MagicMock())
        client = self._make_client(requests_app, mock_status_handler=handler)

        client.get("/requests/req-123/status?long=false")

        call_arg = handler.handle.call_args.args[0]
        assert call_arg["long"] is False

    def test_get_request_details(self, requests_app):
        handler = MagicMock()
        handler.handle = AsyncMock(
            return_value={"requests": [{"request_id": "req-456", "status": "complete"}]}
        )
        client = self._make_client(requests_app, mock_status_handler=handler)

        resp = client.get("/requests/req-456")

        assert resp.status_code == 200
        handler.handle.assert_awaited_once()
        call_arg = handler.handle.call_args.args[0]
        assert call_arg["input_data"]["requests"][0]["requestId"] == "req-456"
        assert call_arg["long"] is True

    def test_cancel_request_happy_path(self, requests_app):
        command_bus = AsyncMock()
        command_bus.execute = AsyncMock(return_value=None)
        client = self._make_client(requests_app, mock_command_bus=command_bus)

        resp = client.delete("/requests/req-789")

        assert resp.status_code == 200
        command_bus.execute.assert_awaited_once()
        cmd = command_bus.execute.call_args.args[0]
        assert isinstance(cmd, CancelRequestCommand)
        assert cmd.request_id == "req-789"

    def test_cancel_request_with_reason(self, requests_app):
        command_bus = AsyncMock()
        command_bus.execute = AsyncMock(return_value=None)
        client = self._make_client(requests_app, mock_command_bus=command_bus)

        resp = client.delete("/requests/req-789?reason=no+longer+needed")

        assert resp.status_code == 200
        cmd = command_bus.execute.call_args.args[0]
        assert cmd.reason == "no longer needed"

    def test_cancel_request_default_reason(self, requests_app):
        command_bus = AsyncMock()
        command_bus.execute = AsyncMock(return_value=None)
        client = self._make_client(requests_app, mock_command_bus=command_bus)

        client.delete("/requests/req-999")

        cmd = command_bus.execute.call_args.args[0]
        assert cmd.reason == "Cancelled via REST API"

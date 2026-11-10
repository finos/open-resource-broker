"""Per-call response format selection (X-ORB-Scheduler header and scheduler query parameter).

Every role may pick any registered format for a call. The response names the
format that produced the body, an unknown name is a client error, and a format
that cannot be built is a server error with no silent fallback.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from orb.api import dependencies as deps
from orb.api.rest_render import render_rest
from orb.api.server import create_fastapi_app
from orb.application.services.orchestration.dtos import ListMachinesOutput
from orb.config.schemas.server_schema import AuthConfig, CORSConfig, ServerConfig
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.infrastructure.scheduler.factory import SchedulerStrategyFactory
from orb.infrastructure.scheduler.registry import get_scheduler_registry
from orb.interface.response_formatting_service import ResponseFormattingService
from tests.utilities.reset_singletons import reset_all_singletons

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_scheduler(name: str = "mock") -> MagicMock:
    scheduler = MagicMock()
    scheduler.name = name
    scheduler.get_scheduler_type.return_value = name
    scheduler.format_request_response.side_effect = lambda raw: {**raw, "scheduler": name}
    scheduler.format_request_status_response.side_effect = lambda reqs: {
        "requests": reqs,
        "scheduler": name,
    }
    scheduler.format_machine_status_response.side_effect = lambda machines: {
        "machines": machines,
        "scheduler": name,
    }
    scheduler.get_exit_code_for_status.return_value = 0
    return scheduler


def _make_formatting_service(scheduler_name: str = "default"):
    return ResponseFormattingService(_make_mock_scheduler(scheduler_name))


class _Container:
    """Minimal container: the configured formatter plus the real strategy factory."""

    def __init__(self, configured: str = "default") -> None:
        self.configured = _make_formatting_service(configured)
        config = MagicMock()
        config.get_scheduler_strategy.return_value = configured
        self._services = {
            ResponseFormattingService: self.configured,
            ConfigurationPort: config,
            SchedulerStrategyFactory: SchedulerStrategyFactory(),
        }

    def get(self, key):
        return self._services[key]


def _register(name: str, build=None) -> None:
    get_scheduler_registry().register(
        name,
        build or (lambda cfg, _n=name: _make_mock_scheduler(_n)),
        lambda c: None,
        strategy_class=MagicMock,
    )


@pytest.fixture
def container():
    yield _Container()
    reset_all_singletons()


@pytest.fixture
def client(container):
    app = create_fastapi_app(
        ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(enabled=False, strategy="replace"),  # type: ignore[call-arg]
            cors=CORSConfig(origins=["*"]),  # type: ignore[call-arg]
        )
    )

    async def probe(formatter=Depends(deps.get_request_formatter)):
        return render_rest("list_machines", formatter, ListMachinesOutput(total_count=0))

    app.add_api_route("/probe", probe, methods=["GET"])
    app.dependency_overrides[deps.get_di_container] = lambda: container
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


class TestFormatSelection:
    def test_no_selection_uses_the_configured_format(self, client):
        response = client.get("/probe")

        assert response.status_code == 200
        assert response.json()["scheduler"] == "default"
        assert response.headers["x-orb-scheduler"] == "default"
        assert "X-ORB-Scheduler" in response.headers["vary"]

    def test_header_selects_a_registered_format(self, client):
        _register("probe-hf")

        response = client.get("/probe", headers={"X-ORB-Scheduler": "probe-hf"})

        assert response.status_code == 200
        assert response.json()["scheduler"] == "probe-hf"
        assert response.headers["x-orb-scheduler"] == "probe-hf"

    def test_query_parameter_selects_a_registered_format(self, client):
        _register("probe-hf")

        response = client.get("/probe?scheduler=probe-hf")

        assert response.json()["scheduler"] == "probe-hf"
        assert response.headers["x-orb-scheduler"] == "probe-hf"

    def test_naming_the_configured_format_returns_the_container_service(self, container):
        request = MagicMock()
        request.headers = {"X-ORB-Scheduler": "default"}
        request.query_params = {}
        if not get_scheduler_registry().is_registered("default"):
            _register("default")

        result = deps.get_request_formatter(request=request, container=container)

        assert result is container.configured

    def test_unknown_format_is_a_client_error(self, client):
        response = client.get("/probe?scheduler=bogus")

        assert response.status_code == 400
        assert response.json()["success"] is False

    def test_header_and_query_that_disagree_are_a_client_error(self, client):
        _register("probe-hf")

        response = client.get("/probe?scheduler=probe-hf", headers={"X-ORB-Scheduler": "default"})

        assert response.status_code == 400

    def test_a_format_that_cannot_be_built_is_a_server_error_without_fallback(self, client):
        def _boom(cfg):
            raise RuntimeError("cannot build")

        _register("probe-broken", _boom)

        response = client.get("/probe", headers={"X-ORB-Scheduler": "probe-broken"})

        assert response.status_code == 500
        assert "scheduler" not in response.json()
        assert "x-orb-scheduler" not in response.headers

    def test_response_header_is_exposed_to_browsers(self, client):
        response = client.get("/probe", headers={"Origin": "http://example.test"})

        assert "X-ORB-Scheduler" in response.headers["access-control-expose-headers"]


class TestRoutesUseTheSelector:
    def test_every_router_formats_through_get_request_formatter(self):
        import ast
        from pathlib import Path

        routers_dir = Path(deps.__file__).parent / "routers"
        for router_file in routers_dir.glob("*.py"):
            names = {
                n.id
                for n in ast.walk(ast.parse(router_file.read_text()))
                if isinstance(n, ast.Name)
            }
            assert "get_response_formatting_service" not in names, router_file.name

    def test_no_router_builds_a_rest_body_from_a_renderer_directly(self):
        from pathlib import Path

        routers_dir = Path(deps.__file__).parent / "routers"
        for router_file in routers_dir.glob("*.py"):
            if router_file.name == "requests.py":
                continue  # the SSE route frames each poll itself
            assert "renderer_for(" not in router_file.read_text(), router_file.name


# ---------------------------------------------------------------------------
# Unit tests: ResponseFormattingService produces scheduler-specific output
# ---------------------------------------------------------------------------


class TestResponseFormattingServiceSchedulerOutput:
    """Verify that swapping the scheduler in ResponseFormattingService changes output.

    This is the core contract that the header feature relies on: different scheduler
    strategies produce different response shapes.
    """

    def test_format_request_operation_uses_injected_scheduler(self):
        """format_request_operation delegates to the injected scheduler."""
        from orb.interface.response_formatting_service import ResponseFormattingService

        scheduler = _make_mock_scheduler("hf")
        svc = ResponseFormattingService(scheduler)

        result = svc.format_request_operation(
            {"request_id": "req-1", "status": "pending"}, "pending"
        )

        scheduler.format_request_response.assert_called_once()
        assert result.data.get("scheduler") == "hf"

    def test_format_request_status_uses_injected_scheduler(self):
        """format_request_status delegates to the injected scheduler."""
        from orb.interface.response_formatting_service import ResponseFormattingService

        scheduler = _make_mock_scheduler("default")
        svc = ResponseFormattingService(scheduler)

        result = svc.format_request_status([{"request_id": "req-1"}])

        scheduler.format_request_status_response.assert_called_once()
        assert result.data.get("scheduler") == "default"

    def test_two_services_with_different_schedulers_produce_different_output(self):
        """Two ResponseFormattingService instances with different schedulers differ in output."""
        from orb.interface.response_formatting_service import ResponseFormattingService

        svc_hf = ResponseFormattingService(_make_mock_scheduler("hostfactory"))
        svc_default = ResponseFormattingService(_make_mock_scheduler("default"))

        raw = {"request_id": "req-x", "status": "pending"}
        out_hf = svc_hf.format_request_operation(raw, "pending")
        out_default = svc_default.format_request_operation(raw, "pending")

        assert out_hf.data.get("scheduler") == "hostfactory"
        assert out_default.data.get("scheduler") == "default"
        assert out_hf.data != out_default.data

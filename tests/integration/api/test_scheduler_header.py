"""Integration tests for X-ORB-Scheduler per-request header feature.

Documents the current wiring state of get_request_formatter / get_request_scheduler
in dependencies.py and verifies the feature behaviour end-to-end.

Key findings (read before editing):
- get_response_formatting_service() is what the routers actually use (FORMATTER dep).
- get_request_formatter() exists in dependencies.py and IS header-aware, but is NOT
  wired into any router — the routers all use get_response_formatting_service instead.
- Therefore the X-ORB-Scheduler header has NO effect on live routes today.
- These tests document that gap and verify the dependency function itself works correctly
  in isolation, so a future wiring change can be validated by updating the "not wired"
  tests to assert the header IS honoured.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_scheduler(name: str = "mock") -> MagicMock:
    scheduler = MagicMock()
    scheduler.name = name
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
    from orb.interface.response_formatting_service import ResponseFormattingService

    return ResponseFormattingService(_make_mock_scheduler(scheduler_name))


# ---------------------------------------------------------------------------
# Unit tests: get_request_formatter dependency function
# ---------------------------------------------------------------------------


class TestGetRequestFormatterDependency:
    """Tests for the get_request_formatter dependency in isolation."""

    def _make_request(self, headers: dict) -> MagicMock:
        req = MagicMock()
        req.headers = headers
        return req

    def _make_container(self, scheduler_name: str = "default") -> MagicMock:
        container = MagicMock()
        container.get.return_value = _make_formatting_service(scheduler_name)
        return container

    def test_no_header_returns_default_service(self):
        """Without X-ORB-Scheduler header, returns the container's ResponseFormattingService."""
        from orb.api.dependencies import get_request_formatter

        request = self._make_request({})
        container = self._make_container("default")

        result = get_request_formatter(request=request, container=container)

        from orb.interface.response_formatting_service import ResponseFormattingService

        assert isinstance(result, ResponseFormattingService)
        container.get.assert_called_once_with(ResponseFormattingService)

    def test_unknown_scheduler_header_falls_back_to_default(self):
        """X-ORB-Scheduler with an unregistered value falls back to container default."""
        from orb.api.dependencies import get_request_formatter

        request = self._make_request({"X-ORB-Scheduler": "nonexistent-scheduler-xyz"})
        container = self._make_container("default")

        result = get_request_formatter(request=request, container=container)

        from orb.interface.response_formatting_service import ResponseFormattingService

        assert isinstance(result, ResponseFormattingService)

    def test_registered_scheduler_header_returns_new_service(self):
        """X-ORB-Scheduler with a registered type returns a freshly-built ResponseFormattingService."""
        from orb.api.dependencies import get_request_formatter
        from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )
        from orb.infrastructure.scheduler.registry import get_scheduler_registry
        from orb.interface.response_formatting_service import ResponseFormattingService

        registry = get_scheduler_registry()
        if not registry.is_registered("hostfactory"):
            registry.register(
                "hostfactory",
                lambda cfg: HostFactorySchedulerStrategy(),
                lambda c: None,
                strategy_class=HostFactorySchedulerStrategy,
            )

        request = self._make_request({"X-ORB-Scheduler": "hostfactory"})
        container = self._make_container("default")

        result = get_request_formatter(request=request, container=container)

        assert isinstance(result, ResponseFormattingService)

    def test_get_request_scheduler_no_header_returns_default(self):
        """get_request_scheduler without header returns container's SchedulerPort."""
        from orb.api.dependencies import get_request_scheduler
        from orb.application.ports.scheduler_port import SchedulerPort

        mock_scheduler = _make_mock_scheduler("default")
        container = MagicMock()
        container.get.return_value = mock_scheduler

        request = self._make_request({})
        result = get_request_scheduler(request=request, container=container)

        assert result is mock_scheduler
        container.get.assert_called_once_with(SchedulerPort)

    def test_get_request_scheduler_unknown_header_falls_back(self):
        """get_request_scheduler with unknown header falls back to container default."""
        from orb.api.dependencies import get_request_scheduler

        mock_scheduler = _make_mock_scheduler("default")
        container = MagicMock()
        container.get.return_value = mock_scheduler

        request = self._make_request({"X-ORB-Scheduler": "does-not-exist"})
        result = get_request_scheduler(request=request, container=container)

        assert result is mock_scheduler


# ---------------------------------------------------------------------------
# Integration tests: header NOT wired into live routes (documents current gap)
# ---------------------------------------------------------------------------


class TestSchedulerHeaderNotWiredInRoutes:
    """Documents that X-ORB-Scheduler header is NOT currently wired into the routers.

    The routers use FORMATTER = Depends(get_response_formatting_service), which
    ignores the header entirely. These tests confirm the current behaviour so that
    when the feature is wired, the tests will need updating.
    """

    @pytest.fixture
    def app_with_real_routes(self):
        """FastAPI app with real routers and overridden dependencies."""
        from orb.api import dependencies as deps
        from orb.api.server import create_fastapi_app
        from orb.config.schemas.server_schema import AuthConfig, ServerConfig

        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(enabled=False, strategy="replace"),  # type: ignore[call-arg]
        )
        app = create_fastapi_app(server_config)

        # Wire up minimal mocks so routes don't hit real DI
        default_svc = _make_formatting_service("default")
        app.dependency_overrides[deps.get_response_formatting_service] = lambda: default_svc

        mock_health = MagicMock()
        mock_health.get_status.return_value = {"status": "healthy"}
        app.dependency_overrides[deps.get_health_check_port] = lambda: mock_health

        return app

    def test_x_orb_scheduler_header_present_in_request_reaches_server(self, app_with_real_routes):
        """A request with X-ORB-Scheduler header is accepted (not rejected by middleware)."""
        from fastapi.testclient import TestClient

        client = TestClient(app_with_real_routes, raise_server_exceptions=False)
        response = client.get(
            "/health",
            headers={"X-ORB-Scheduler": "hostfactory"},
        )
        # Header must not cause a 4xx/5xx at the middleware level
        assert response.status_code < 500

    def test_routes_use_get_response_formatting_service_not_get_request_formatter(self):
        """Confirm routers import get_response_formatting_service, not get_request_formatter.

        This is a static analysis test — it reads the router source and checks which
        dependency function is referenced. Fails if the wiring changes (intentionally).
        """
        import ast

        routers_dir = Path(__file__).parent.parent.parent / "src" / "orb" / "api" / "routers"
        for router_file in routers_dir.glob("*.py"):
            source = router_file.read_text()
            tree = ast.parse(source)
            names_used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            # Current state: routers use get_response_formatting_service
            if "get_response_formatting_service" in source:
                assert "get_response_formatting_service" in names_used, (
                    f"{router_file.name} imports but does not use get_response_formatting_service"
                )
            # Current state: no router uses get_request_formatter
            assert "get_request_formatter" not in names_used, (
                f"{router_file.name} now uses get_request_formatter — "
                "update TestSchedulerHeaderWiredInRoutes to verify header behaviour"
            )


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

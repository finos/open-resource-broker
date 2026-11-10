"""GET /providers/ renders the catalog body and keeps the legacy REST keys."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import CurrentUser, get_current_user
from orb.api.models.responses import ProviderListResponse
from orb.api.routers.providers import router as providers_router
from orb.application.services.orchestration.dtos import ListProvidersOutput
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.interface.catalog import OPERATION_CATALOG, Interface
from orb.interface.response_formatting_service import ResponseFormattingService
from tests.unit.api.providers_support import serve_providers_from


def _provider(name: str, enabled: bool = True) -> MagicMock:
    p = MagicMock()
    p.name, p.type, p.enabled = name, "aws", enabled
    p.config = {"region": "eu-west-2"}
    p.weight, p.priority = 5, 1
    p.get_effective_handlers.return_value = {"EC2Fleet": {}}
    return p


def _config(*providers: MagicMock) -> MagicMock:
    provider_config = MagicMock()
    provider_config.get_active_providers.return_value = list(providers)
    provider_config.provider_defaults = {}
    provider_config.selection_policy = "weighted_round_robin"
    manager = MagicMock()
    manager.get_provider_config.return_value = provider_config
    return manager


def _client(config: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(providers_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(username="v", role="viewer")
    serve_providers_from(app, config)
    return TestClient(app, raise_server_exceptions=False)


def test_body_validates_against_the_declared_model_and_keeps_legacy_keys():
    body = (
        _client(_config(_provider("aws-1"), _provider("aws-2", enabled=False)))
        .get("/providers/")
        .json()
    )

    parsed = ProviderListResponse.model_validate(body)
    assert parsed.count == parsed.total_count == 2
    assert parsed.selection_policy == "weighted_round_robin"
    first = parsed.providers[0]
    assert (first.name, first.type, first.enabled) == ("aws-1", "aws", True)
    assert first.config == {"region": "eu-west-2"}
    assert first.capabilities == ["EC2Fleet"]
    assert parsed.providers[1].enabled is False
    assert parsed.providers[1].status == "disabled"


def test_rest_body_equals_the_catalog_renderer_output_for_every_interface():
    output = ListProvidersOutput(
        providers=[{"name": "aws-1", "type": "aws"}], count=1, selection_policy="p", message="m"
    )
    formatter = ResponseFormattingService(DefaultSchedulerStrategy(logger=MagicMock()))
    entry = OPERATION_CATALOG["list_providers"]

    bodies = {i: entry.renderer_for(i)(formatter, output).data for i in entry.exposed_on}

    assert Interface.REST in bodies and Interface.CLI in bodies and Interface.MCP in bodies
    assert all(body == bodies[Interface.REST] for body in bodies.values())
    assert bodies[Interface.CLI]["total_count"] == bodies[Interface.CLI]["count"] == 1

"""Wire GET /providers/ to the real orchestrator and handler over a fake configuration."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI

from orb.api.dependencies import get_list_providers_orchestrator, get_request_formatter
from orb.application.queries.provider_handlers import ListAvailableProvidersHandler
from orb.application.services.orchestration.list_providers import ListProvidersOrchestrator
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.interface.response_formatting_service import ResponseFormattingService


def serve_providers_from(app: FastAPI, config_mgr: Any) -> None:
    """Make ``GET /providers/`` list the providers of ``config_mgr``."""
    handler = ListAvailableProvidersHandler(config_mgr, MagicMock(), MagicMock(), MagicMock())
    query_bus = MagicMock()
    query_bus.execute = AsyncMock(side_effect=handler.execute_query)
    orchestrator = ListProvidersOrchestrator(query_bus, MagicMock())
    formatter = ResponseFormattingService(DefaultSchedulerStrategy(logger=MagicMock()))
    app.dependency_overrides[get_list_providers_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_request_formatter] = lambda: formatter

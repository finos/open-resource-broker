"""Health-check contracts for dry-run and Azure authentication failures."""

from unittest.mock import MagicMock

import pytest

from orb.infrastructure.mocking.dry_run_context import dry_run_context
from orb.providers.azure.services.health_check_service import AzureHealthCheckService


@pytest.fixture
def service(azure_config, logger) -> AzureHealthCheckService:
    return AzureHealthCheckService(config=azure_config, logger=logger)


def test_sync_dry_run_reports_healthy_without_requesting_a_token(
    service: AzureHealthCheckService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_provider = MagicMock()
    monkeypatch.setattr(
        "orb.providers.azure.services.health_check_service.DefaultAzureAccessTokenProvider",
        token_provider,
    )

    with dry_run_context():
        result = service.check_health()

    assert result.is_healthy is True
    assert "DRY-RUN" in result.status_message
    assert "eastus2" in result.status_message
    token_provider.assert_not_called()


def test_sync_auth_failure_is_returned_as_unhealthy_status(
    service: AzureHealthCheckService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MagicMock()
    provider.get_access_token.side_effect = RuntimeError("credential unavailable")
    monkeypatch.setattr(
        "orb.providers.azure.services.health_check_service.DefaultAzureAccessTokenProvider",
        MagicMock(return_value=provider),
    )

    result = service.check_health()

    assert result.is_healthy is False
    assert result.status_message == "Health check error: credential unavailable"
    assert result.error_details is not None
    assert result.error_details["error"] == "credential unavailable"
    assert result.error_details["response_time_ms"] >= 0


@pytest.mark.asyncio
async def test_async_dry_run_reports_healthy_without_requesting_a_token(
    service: AzureHealthCheckService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_provider = MagicMock()
    monkeypatch.setattr(
        "orb.providers.azure.services.health_check_service.AsyncDefaultAzureAccessTokenProvider",
        token_provider,
    )

    with dry_run_context():
        result = await service.check_health_async()

    assert result.is_healthy is True
    assert "DRY-RUN" in result.status_message
    assert "eastus2" in result.status_message
    token_provider.assert_not_called()


@pytest.mark.asyncio
async def test_async_auth_failure_is_returned_as_unhealthy_status(
    service: AzureHealthCheckService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MagicMock()
    provider.get_access_token.side_effect = RuntimeError("credential unavailable")
    monkeypatch.setattr(
        "orb.providers.azure.services.health_check_service.AsyncDefaultAzureAccessTokenProvider",
        MagicMock(return_value=provider),
    )

    result = await service.check_health_async()

    assert result.is_healthy is False
    assert result.status_message == "Health check error: credential unavailable"
    assert result.error_details is not None
    assert result.error_details["error"] == "credential unavailable"
    assert result.error_details["response_time_ms"] >= 0

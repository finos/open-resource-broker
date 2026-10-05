"""Unit tests for ProviderRegistryService — thin delegation to ProviderRegistryPort."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.provider_registry_service import ProviderRegistryService


def _make_service():
    registry = MagicMock()
    validation_service = MagicMock()
    logger = MagicMock()
    service = ProviderRegistryService(
        registry=registry, validation_service=validation_service, logger=logger
    )
    return service, registry, validation_service, logger


@pytest.mark.unit
class TestProviderRegistryService:
    def test_select_provider_for_template_delegates(self):
        service, registry, _, logger = _make_service()
        registry.select_provider_for_template.return_value = "selection-result"
        template = MagicMock()

        result = service.select_provider_for_template(template, provider_name="aws-1")

        assert result == "selection-result"
        registry.select_provider_for_template.assert_called_once_with(template, "aws-1", logger)

    def test_select_active_provider_delegates(self):
        service, registry, _, logger = _make_service()
        registry.select_active_provider.return_value = "active-provider"

        result = service.select_active_provider(provider_name="aws-1", provider_type="aws")

        assert result == "active-provider"
        registry.select_active_provider.assert_called_once_with(
            logger, provider_name="aws-1", provider_type="aws"
        )

    def test_validate_template_requirements_delegates_to_validation_service(self):
        service, _, validation_service, _ = _make_service()
        validation_service.validate_template_requirements.return_value = "validation-result"
        template = MagicMock()

        result = service.validate_template_requirements(template, "aws-1")

        assert result == "validation-result"
        validation_service.validate_template_requirements.assert_called_once_with(template, "aws-1")

    @pytest.mark.asyncio
    async def test_execute_operation_delegates_to_strategy(self):
        service, registry, _, _ = _make_service()
        strategy = MagicMock()
        strategy.execute_operation = AsyncMock(return_value="op-result")
        registry.get_or_create_strategy.return_value = strategy
        operation = MagicMock()

        result = await service.execute_operation("aws-1", operation)

        assert result == "op-result"
        strategy.execute_operation.assert_awaited_once_with(operation)

    @pytest.mark.asyncio
    async def test_execute_operation_raises_when_no_strategy(self):
        service, registry, _, _ = _make_service()
        registry.get_or_create_strategy.return_value = None

        with pytest.raises(ValueError, match="No strategy found"):
            await service.execute_operation("missing-provider", MagicMock())

    def test_get_strategy_capabilities_returns_none_when_no_strategy(self):
        service, registry, _, _ = _make_service()
        registry.get_or_create_strategy.return_value = None

        assert service.get_strategy_capabilities("missing-provider") is None

    def test_get_strategy_capabilities_delegates_when_strategy_exists(self):
        service, registry, _, _ = _make_service()
        strategy = MagicMock()
        strategy.get_capabilities.return_value = {"supports_spot": True}
        registry.get_or_create_strategy.return_value = strategy

        result = service.get_strategy_capabilities("aws-1")

        assert result == {"supports_spot": True}

    def test_get_available_strategies_combines_providers_and_instances(self):
        service, registry, _, _ = _make_service()
        registry.get_registered_providers.return_value = ["aws"]
        registry.get_registered_provider_instances.return_value = ["aws-1", "aws-2"]

        result = service.get_available_strategies()

        assert result == ["aws", "aws-1", "aws-2"]

    def test_register_provider_strategy_delegates(self):
        service, registry, _, _ = _make_service()
        registry.ensure_provider_type_registered.return_value = True

        assert service.register_provider_strategy("aws") is True
        registry.ensure_provider_type_registered.assert_called_once_with("aws")

    def test_check_strategy_health_returns_none_when_no_strategy(self):
        service, registry, _, _ = _make_service()
        registry.get_or_create_strategy.return_value = None

        assert service.check_strategy_health("missing-provider") is None

    def test_check_strategy_health_delegates_when_strategy_exists(self):
        service, registry, _, _ = _make_service()
        strategy = MagicMock()
        strategy.check_health.return_value = {"status": "healthy"}
        registry.get_or_create_strategy.return_value = strategy

        result = service.check_strategy_health("aws-1")

        assert result == {"status": "healthy"}

    def test_update_provider_health_delegates(self):
        service, registry, _, _ = _make_service()

        service.update_provider_health("aws-1", {"status": "degraded"})

        registry.update_provider_health.assert_called_once_with("aws-1", {"status": "degraded"})

    def test_resolve_api_alias_returns_raw_when_no_strategy(self):
        service, registry, _, _ = _make_service()
        registry.get_or_create_strategy.return_value = None

        result = service.resolve_api_alias("missing-provider", "RunInstances")

        assert result == "RunInstances"

    def test_resolve_api_alias_delegates_when_strategy_exists(self):
        service, registry, _, _ = _make_service()
        strategy = MagicMock()
        strategy.resolve_api_alias.return_value = "EC2Fleet"
        registry.get_or_create_strategy.return_value = strategy

        result = service.resolve_api_alias("aws-1", "ec2fleet")

        assert result == "EC2Fleet"

    def test_get_registered_provider_types_delegates(self):
        service, registry, _, _ = _make_service()
        registry.get_registered_providers.return_value = ["aws", "azure"]

        assert service.get_registered_provider_types() == ["aws", "azure"]

    def test_ensure_provider_registered_delegates(self):
        service, registry, _, _ = _make_service()
        registry.ensure_provider_type_registered.return_value = False

        assert service.ensure_provider_registered("gcp") is False

    def test_get_or_create_strategy_delegates_with_config(self):
        service, registry, _, _ = _make_service()
        registry.get_or_create_strategy.return_value = "strategy-instance"
        config = {"region": "us-east-1"}

        result = service.get_or_create_strategy("aws-1", config)

        assert result == "strategy-instance"
        registry.get_or_create_strategy.assert_called_once_with("aws-1", config)

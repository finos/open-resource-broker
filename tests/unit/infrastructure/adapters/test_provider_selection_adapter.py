"""Unit tests for ProviderSelectionAdapter."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.infrastructure.adapters.provider_selection_adapter import ProviderSelectionAdapter

pytestmark = pytest.mark.unit


def _make_adapter() -> tuple[ProviderSelectionAdapter, MagicMock]:
    service = MagicMock()
    adapter = ProviderSelectionAdapter(provider_registry_service=service)
    return adapter, service


class TestSelectProviderForTemplate:
    def test_delegates_to_service(self):
        adapter, service = _make_adapter()
        template = MagicMock()
        expected = MagicMock()
        service.select_provider_for_template.return_value = expected

        result = adapter.select_provider_for_template(template, provider_name="aws")

        service.select_provider_for_template.assert_called_once_with(template, "aws")
        assert result is expected

    def test_defaults_provider_name_to_none(self):
        adapter, service = _make_adapter()
        template = MagicMock()
        adapter.select_provider_for_template(template)
        service.select_provider_for_template.assert_called_once_with(template, None)


class TestSelectActiveProvider:
    def test_delegates_to_service_with_keyword_args(self):
        adapter, service = _make_adapter()
        expected = MagicMock()
        service.select_active_provider.return_value = expected

        result = adapter.select_active_provider(provider_name="aws", provider_type="ec2")

        service.select_active_provider.assert_called_once_with(
            provider_name="aws", provider_type="ec2"
        )
        assert result is expected

    def test_defaults_are_none(self):
        adapter, service = _make_adapter()
        adapter.select_active_provider()
        service.select_active_provider.assert_called_once_with(
            provider_name=None, provider_type=None
        )


class TestValidateTemplateRequirements:
    def test_delegates_to_service(self):
        adapter, service = _make_adapter()
        template = MagicMock()
        expected = MagicMock()
        service.validate_template_requirements.return_value = expected

        result = adapter.validate_template_requirements(template, "aws-1")

        service.validate_template_requirements.assert_called_once_with(template, "aws-1")
        assert result is expected


class TestExecuteOperation:
    @pytest.mark.asyncio
    async def test_awaits_service_execution(self):
        adapter, service = _make_adapter()
        service.execute_operation = AsyncMock(return_value="done")

        result = await adapter.execute_operation("provider-1", operation="op")

        service.execute_operation.assert_awaited_once_with("provider-1", "op")
        assert result == "done"


class TestGetStrategyCapabilities:
    def test_delegates_to_service(self):
        adapter, service = _make_adapter()
        service.get_strategy_capabilities.return_value = {"cap": True}

        result = adapter.get_strategy_capabilities("provider-1")

        service.get_strategy_capabilities.assert_called_once_with("provider-1")
        assert result == {"cap": True}


class TestGetAvailableStrategies:
    def test_delegates_to_service(self):
        adapter, service = _make_adapter()
        service.get_available_strategies.return_value = ["aws", "k8s"]

        result = adapter.get_available_strategies()

        service.get_available_strategies.assert_called_once_with()
        assert result == ["aws", "k8s"]


class TestRegisterProviderStrategy:
    def test_delegates_to_service_with_config(self):
        adapter, service = _make_adapter()
        service.register_provider_strategy.return_value = True

        result = adapter.register_provider_strategy("aws", config={"a": 1})

        service.register_provider_strategy.assert_called_once_with("aws", {"a": 1})
        assert result is True

    def test_defaults_config_to_none(self):
        adapter, service = _make_adapter()
        adapter.register_provider_strategy("aws")
        service.register_provider_strategy.assert_called_once_with("aws", None)


class TestCheckStrategyHealth:
    def test_delegates_to_service(self):
        adapter, service = _make_adapter()
        service.check_strategy_health.return_value = {"healthy": True}

        result = adapter.check_strategy_health("provider-1")

        service.check_strategy_health.assert_called_once_with("provider-1")
        assert result == {"healthy": True}

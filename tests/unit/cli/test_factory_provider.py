"""Unit tests for orb.cli.factories.provider_command_factory.ProviderCommandFactory.

Tests cover query/command construction; JSON-parsing branches in
create_execute_provider_operation_command are fully exercised.
"""

from __future__ import annotations

import pytest

from orb.cli.factories.provider_command_factory import ProviderCommandFactory


@pytest.fixture
def factory() -> ProviderCommandFactory:
    return ProviderCommandFactory()


@pytest.mark.unit
class TestCreateGetProviderHealthQuery:
    def test_provider_name_defaults_to_none(self, factory):
        q = factory.create_get_provider_health_query()
        assert q.provider_name is None

    def test_provider_name_set(self, factory):
        q = factory.create_get_provider_health_query(provider_name="aws-prod")
        assert q.provider_name == "aws-prod"


@pytest.mark.unit
class TestCreateGetProviderMetricsQuery:
    def test_defaults(self, factory):
        q = factory.create_get_provider_metrics_query()
        assert q.timeframe == "24h"
        assert q.provider_name is None

    def test_timeframe_set(self, factory):
        q = factory.create_get_provider_metrics_query(timeframe="7d")
        assert q.timeframe == "7d"

    def test_provider_name_set(self, factory):
        q = factory.create_get_provider_metrics_query(provider_name="aws-dev")
        assert q.provider_name == "aws-dev"


@pytest.mark.unit
class TestCreateListAvailableProvidersQuery:
    def test_defaults(self, factory):
        q = factory.create_list_available_providers_query()
        assert q.include_health is False
        assert q.include_capabilities is False
        assert q.include_metrics is False
        assert q.filter_healthy_only is False
        assert q.provider_type is None
        assert q.filter_expressions == []

    def test_include_health(self, factory):
        q = factory.create_list_available_providers_query(include_health=True)
        assert q.include_health is True

    def test_provider_type_set(self, factory):
        q = factory.create_list_available_providers_query(provider_type="k8s")
        assert q.provider_type == "k8s"

    def test_filter_expressions_none_normalised(self, factory):
        q = factory.create_list_available_providers_query(filter_expressions=None)
        assert q.filter_expressions == []

    def test_filter_healthy_only(self, factory):
        q = factory.create_list_available_providers_query(filter_healthy_only=True)
        assert q.filter_healthy_only is True


@pytest.mark.unit
class TestCreateGetProviderCapabilitiesQuery:
    def test_provider_name_required(self, factory):
        q = factory.create_get_provider_capabilities_query(provider_name="aws-prod")
        assert q.provider_name == "aws-prod"

    def test_defaults(self, factory):
        q = factory.create_get_provider_capabilities_query(provider_name="x")
        assert q.include_performance_metrics is True
        assert q.include_limitations is True

    def test_flags_set_false(self, factory):
        q = factory.create_get_provider_capabilities_query(
            provider_name="x",
            include_performance_metrics=False,
            include_limitations=False,
        )
        assert q.include_performance_metrics is False
        assert q.include_limitations is False


@pytest.mark.unit
class TestCreateGetProviderStrategyConfigQuery:
    def test_returns_query_instance(self, factory):
        from orb.application.provider.queries import GetProviderStrategyConfigQuery

        q = factory.create_get_provider_strategy_config_query()
        assert isinstance(q, GetProviderStrategyConfigQuery)


@pytest.mark.unit
class TestCreateExecuteProviderOperationCommand:
    def test_valid_operation_no_params(self, factory):
        cmd = factory.create_execute_provider_operation_command(operation="health_check")
        from orb.application.provider.commands import ExecuteProviderOperationCommand

        assert isinstance(cmd, ExecuteProviderOperationCommand)

    def test_valid_json_params_parsed(self, factory):
        cmd = factory.create_execute_provider_operation_command(
            operation="health_check",
            params='{"region": "us-east-1"}',
        )
        assert cmd.operation.parameters == {"region": "us-east-1"}

    def test_invalid_json_params_raises_value_error(self, factory):
        with pytest.raises(ValueError, match="Invalid JSON in params"):
            factory.create_execute_provider_operation_command(
                operation="health_check",
                params="not-valid-json",
            )

    def test_provider_name_in_context(self, factory):
        cmd = factory.create_execute_provider_operation_command(
            operation="health_check",
            provider_name="aws-prod",
        )
        assert cmd.operation.context == {"provider_override": "aws-prod"}

    def test_no_provider_name_empty_context(self, factory):
        cmd = factory.create_execute_provider_operation_command(
            operation="health_check",
        )
        assert cmd.operation.context == {}

    def test_provider_name_threads_into_strategy_override_and_context(self, factory):
        # By contract the factory routes provider_name to BOTH the command-level
        # strategy_override and the operation context's provider_override, so a
        # single --provider flag drives strategy selection and operation routing.
        cmd = factory.create_execute_provider_operation_command(
            operation="health_check",
            provider_name="aws-prod",
        )
        assert cmd.strategy_override == "aws-prod"
        assert cmd.operation.context == {"provider_override": "aws-prod"}

    def test_no_provider_name_leaves_strategy_override_unset(self, factory):
        # When no provider is given, strategy_override must be None (not "" or a
        # default), so provider selection falls back to configuration.
        cmd = factory.create_execute_provider_operation_command(operation="health_check")
        assert cmd.strategy_override is None

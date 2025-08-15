"""Unit tests for provider strategy queries."""

from application.provider.queries import (
    GetProviderCapabilitiesQuery,
    GetProviderHealthQuery,
    GetProviderMetricsQuery,
    GetProviderStrategyConfigQuery,
    ListAvailableProvidersQuery,
)


class TestProviderStrategyQueries:
    """Test provider strategy query creation and validation."""

    def test_get_provider_health_query_creation(self):
        """Test GetProviderHealthQuery creation."""
        query = GetProviderHealthQuery(
            provider_name="aws-primary", include_details=True, include_history=False
        )

        assert query.provider_name == "aws-primary"
        assert query.include_details is True
        assert query.include_history is False

    def test_get_provider_health_query_defaults(self):
        """Test GetProviderHealthQuery with defaults."""
        query = GetProviderHealthQuery()

        assert query.provider_name is None
        assert query.include_details is True
        assert query.include_history is False

    def test_list_available_providers_query_creation(self):
        """Test ListAvailableProvidersQuery creation."""
        query = ListAvailableProvidersQuery(
            include_health=True,
            include_capabilities=False,
            include_metrics=True,
            filter_healthy_only=True,
            provider_type="aws",
        )

        assert query.include_health is True
        assert query.include_capabilities is False
        assert query.include_metrics is True
        assert query.filter_healthy_only is True
        assert query.provider_type == "aws"

    def test_get_provider_capabilities_query_creation(self):
        """Test GetProviderCapabilitiesQuery creation."""
        query = GetProviderCapabilitiesQuery(
            provider_name="aws-primary",
            include_performance_metrics=False,
            include_limitations=True,
        )

        assert query.provider_name == "aws-primary"
        assert query.include_performance_metrics is False
        assert query.include_limitations is True

    def test_get_provider_metrics_query_creation(self):
        """Test GetProviderMetricsQuery creation."""
        query = GetProviderMetricsQuery(
            provider_name="aws-primary",
            time_range_hours=48,
            include_operation_breakdown=False,
            include_error_details=True,
        )

        assert query.provider_name == "aws-primary"
        assert query.time_range_hours == 48
        assert query.include_operation_breakdown is False
        assert query.include_error_details is True

    def test_get_provider_strategy_config_query_creation(self):
        """Test GetProviderStrategyConfigQuery creation."""
        query = GetProviderStrategyConfigQuery(
            include_selection_policies=False,
            include_fallback_config=True,
            include_health_check_config=False,
            include_circuit_breaker_config=True,
        )

        assert query.include_selection_policies is False
        assert query.include_fallback_config is True
        assert query.include_health_check_config is False
        assert query.include_circuit_breaker_config is True

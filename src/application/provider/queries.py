"""Provider Strategy Queries - CQRS queries for provider strategy information.

This module defines queries for retrieving provider strategy information,
including health status, capabilities, metrics, and configuration.
"""

from typing import Optional

from application.dto.base import BaseQuery


class GetProviderHealthQuery(BaseQuery):
    """Query to get provider health status."""

    provider_name: Optional[str] = None  # None = all providers
    include_details: bool = True
    include_history: bool = False


class ListAvailableProvidersQuery(BaseQuery):
    """Query to list all available provider strategies."""

    include_health: bool = True
    include_capabilities: bool = True
    include_metrics: bool = False
    filter_healthy_only: bool = False
    provider_type: Optional[str] = None


class GetProviderCapabilitiesQuery(BaseQuery):
    """Query to get provider capabilities and features."""

    provider_name: str
    include_performance_metrics: bool = True
    include_limitations: bool = True


class GetProviderMetricsQuery(BaseQuery):
    """Query to get provider performance metrics."""

    provider_name: Optional[str] = None  # None = all providers
    time_range_hours: int = 24
    include_operation_breakdown: bool = True
    include_error_details: bool = False


class GetProviderStrategyConfigQuery(BaseQuery):
    """Query to get current provider strategy configuration."""

    include_selection_policies: bool = True
    include_fallback_config: bool = True
    include_health_check_config: bool = True
    include_circuit_breaker_config: bool = True

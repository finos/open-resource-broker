"""Provider command factory for creating provider-related commands and queries."""

from typing import Any, Optional

from orb.application.provider.commands import ExecuteProviderOperationCommand
from orb.application.provider.queries import (
    GetProviderCapabilitiesQuery,
    GetProviderHealthQuery,
    GetProviderStrategyConfigQuery,
    ListAvailableProvidersQuery,
)
from orb.domain.base.operations import (
    Operation as ProviderOperation,
    OperationType as ProviderOperationType,
)
from orb.infrastructure.utilities.json_utils import JSONParseError, safe_json_loads


class ProviderCommandFactory:
    """Factory for creating provider-related commands and queries."""

    def create_get_provider_health_query(
        self,
        provider_name: Optional[str] = None,
        include_details: bool = True,
        include_metrics: bool = False,
        filter_healthy_only: bool = False,
        provider_type: Optional[str] = None,
        filter_expressions: Optional[list] = None,
        **kwargs: Any,
    ) -> GetProviderHealthQuery:
        """Create query to get provider health."""
        return GetProviderHealthQuery(
            provider_name=provider_name,
            include_details=include_details,
        )

    def create_list_available_providers_query(
        self,
        include_health: bool = False,
        include_capabilities: bool = False,
        include_metrics: bool = False,
        filter_healthy_only: bool = False,
        provider_type: Optional[str] = None,
        filter_expressions: Optional[list] = None,
        provider_name: Optional[str] = None,
        **kwargs: Any,
    ) -> ListAvailableProvidersQuery:
        """Create query to list available providers."""
        return ListAvailableProvidersQuery(
            include_health=include_health,
            include_capabilities=include_capabilities,
            include_metrics=include_metrics,
            filter_healthy_only=filter_healthy_only,
            provider_type=provider_type,
            filter_expressions=filter_expressions or [],
            provider_name=provider_name,
        )

    def create_get_provider_capabilities_query(
        self,
        provider_name: str,
        include_performance_metrics: bool = True,
        include_limitations: bool = True,
        **kwargs: Any,
    ) -> GetProviderCapabilitiesQuery:
        """Create query to get provider capabilities."""
        return GetProviderCapabilitiesQuery(
            provider_name=provider_name,
            include_performance_metrics=include_performance_metrics,
            include_limitations=include_limitations,
        )

    def create_get_provider_strategy_config_query(
        self,
        include_selection_policies: bool = True,
        include_fallback_config: bool = True,
        include_health_check_config: bool = True,
        include_circuit_breaker_config: bool = True,
        **kwargs: Any,
    ) -> GetProviderStrategyConfigQuery:
        """Create query to get provider strategy configuration."""
        return GetProviderStrategyConfigQuery(
            include_selection_policies=include_selection_policies,
            include_fallback_config=include_fallback_config,
            include_health_check_config=include_health_check_config,
            include_circuit_breaker_config=include_circuit_breaker_config,
        )

    def create_execute_provider_operation_command(
        self,
        operation: str,
        params: Optional[str] = None,
        provider_name: Optional[str] = None,
        **kwargs: Any,
    ) -> ExecuteProviderOperationCommand:
        """Create command to execute provider operation."""
        # Parse params if provided
        parsed_params = {}
        if params:
            try:
                parsed_params = safe_json_loads(
                    params, raise_on_error=True, context="Provider operation params"
                )
            except JSONParseError as e:
                raise ValueError(f"Invalid JSON in params: {params}") from e

        # Create ProviderOperation
        provider_operation = ProviderOperation(
            operation_type=ProviderOperationType(operation),
            parameters=parsed_params,
            context={"provider_override": provider_name} if provider_name else {},
        )

        return ExecuteProviderOperationCommand(
            operation=provider_operation, strategy_override=provider_name
        )

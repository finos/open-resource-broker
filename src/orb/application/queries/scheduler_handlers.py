"""Scheduler query handlers for administrative operations."""

from orb.application.base.handlers import BaseQueryHandler
from orb.application.decorators import query_handler
from orb.application.dto.system import (
    SchedulerConfigurationResponse,
    SchedulerStrategyDTO,
    SchedulerStrategyListResponse,
    ValidationResultDTO,
)
from orb.application.queries.scheduler import (
    GetSchedulerConfigurationQuery,
    ListSchedulerStrategiesQuery,
    ValidateSchedulerConfigurationQuery,
)
from orb.application.services.scheduler_registry_service import SchedulerRegistryService
from orb.domain.base.ports.error_handling_port import ErrorHandlingPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.services.generic_filter_service import GenericFilterService


@query_handler(ListSchedulerStrategiesQuery)
class ListSchedulerStrategiesHandler(
    BaseQueryHandler[ListSchedulerStrategiesQuery, SchedulerStrategyListResponse]
):
    """Handler for listing available scheduler strategies."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        scheduler_service: SchedulerRegistryService,
        generic_filter_service: GenericFilterService,
    ):
        super().__init__(logger, error_handler)
        self._scheduler_service = scheduler_service
        self._generic_filter_service = generic_filter_service

    async def execute_query(
        self, query: ListSchedulerStrategiesQuery
    ) -> SchedulerStrategyListResponse:
        """
        Execute scheduler strategies list query.

        Args:
            query: List scheduler strategies query

        Returns:
            Scheduler strategies list response
        """
        from orb.config.manager import ConfigurationManager

        scheduler_types = self._scheduler_service.get_available_schedulers()

        strategies = []
        current_strategy = "unknown"

        if query.include_current:
            try:
                config_manager = ConfigurationManager()
                current_strategy = config_manager.get_scheduler_strategy()
            except Exception:
                current_strategy = "unknown"

        for scheduler_type in scheduler_types:
            strategy_info = SchedulerStrategyDTO(
                name=scheduler_type,
                active=(scheduler_type == current_strategy if query.include_current else False),
                registered=True,
                description=(
                    self._get_scheduler_description(scheduler_type)
                    if query.include_details
                    else None
                ),
                capabilities=(
                    self._get_scheduler_capabilities(scheduler_type)
                    if query.include_details
                    else []
                ),
            )
            strategies.append(strategy_info)

        # Convert DTO objects to dictionaries for filtering
        strategies_dict = [strategy.model_dump() for strategy in strategies]

        # Apply generic filters if provided
        if query.filter_expressions:
            strategies_dict = self._generic_filter_service.apply_filters(
                strategies_dict, query.filter_expressions
            )

        # Convert back to DTO objects
        filtered_strategies = [SchedulerStrategyDTO(**strategy) for strategy in strategies_dict]

        return SchedulerStrategyListResponse(
            strategies=filtered_strategies,
            current_strategy=current_strategy,
            total_count=len(filtered_strategies),
        )

    def _get_scheduler_description(self, scheduler_type: str) -> str:
        """Get description for scheduler type."""
        descriptions = {
            "default": "Default scheduler using native domain fields without conversion",
            "hostfactory": "Symphony HostFactory scheduler with field mapping and conversion",
            "hf": "Alias for Symphony HostFactory scheduler",
        }
        return descriptions.get(scheduler_type, f"Scheduler strategy: {scheduler_type}")

    def _get_scheduler_capabilities(self, scheduler_type: str) -> list[str]:
        """Get capabilities for scheduler type."""
        capabilities = {
            "default": [
                "native_domain_format",
                "direct_serialization",
                "minimal_conversion",
            ],
            "hostfactory": [
                "field_mapping",
                "format_conversion",
                "legacy_compatibility",
            ],
            "hf": ["field_mapping", "format_conversion", "legacy_compatibility"],
        }
        return capabilities.get(scheduler_type, [])


@query_handler(GetSchedulerConfigurationQuery)
class GetSchedulerConfigurationHandler(
    BaseQueryHandler[GetSchedulerConfigurationQuery, SchedulerConfigurationResponse]
):
    """Handler for getting scheduler configuration."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        scheduler_service: SchedulerRegistryService,
    ):
        super().__init__(logger, error_handler)
        self._scheduler_service = scheduler_service

    async def execute_query(
        self, query: GetSchedulerConfigurationQuery
    ) -> SchedulerConfigurationResponse:
        """
        Execute scheduler configuration query.

        Args:
            query: Get scheduler configuration query

        Returns:
            Scheduler configuration response
        """
        from orb.config.manager import ConfigurationManager

        config_manager = ConfigurationManager()

        if query.scheduler_name:
            scheduler_name = query.scheduler_name
            is_active = scheduler_name == config_manager.get_scheduler_strategy()
        else:
            scheduler_name = config_manager.get_scheduler_strategy()
            is_active = True

        # Check if scheduler is registered
        is_registered = self._scheduler_service.is_scheduler_registered(scheduler_name)

        # Get configuration details
        configuration = {}
        found = False

        try:
            configuration = config_manager.app_config.scheduler.model_dump()
            found = True
        except Exception:
            configuration = {"error": "Failed to load scheduler configuration"}

        return SchedulerConfigurationResponse(
            scheduler_name=scheduler_name,
            configuration=configuration,
            active=is_active,
            valid=is_registered and found,
            found=found,
        )


@query_handler(ValidateSchedulerConfigurationQuery)
class ValidateSchedulerConfigurationHandler(
    BaseQueryHandler[ValidateSchedulerConfigurationQuery, ValidationResultDTO]
):
    """Handler for validating scheduler configuration."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        scheduler_service: SchedulerRegistryService,
    ):
        super().__init__(logger, error_handler)
        self._scheduler_service = scheduler_service

    async def execute_query(
        self, query: ValidateSchedulerConfigurationQuery
    ) -> ValidationResultDTO:
        """
        Execute scheduler configuration validation query.

        Args:
            query: Validate scheduler configuration query

        Returns:
            Validation result
        """
        from orb.config.manager import ConfigurationManager

        config_manager = ConfigurationManager()

        errors = []
        warnings = []

        try:
            if query.scheduler_name:
                scheduler_name = query.scheduler_name
            else:
                scheduler_name = config_manager.get_scheduler_strategy()

            # Check if scheduler is registered
            available_schedulers = self._scheduler_service.get_available_schedulers()
            if scheduler_name not in available_schedulers:
                errors.append(
                    f"Scheduler '{scheduler_name}' is not registered. Available: {', '.join(available_schedulers)}"
                )

            # Try to create scheduler strategy
            try:
                strategy = self._scheduler_service.create_scheduler_strategy(
                    scheduler_name, config_manager
                )
                if strategy is None:
                    errors.append(f"Failed to create scheduler strategy '{scheduler_name}'")
            except Exception as e:
                errors.append(f"Scheduler strategy creation failed: {e!s}")

            # Check configuration completeness
            try:
                scheduler_config = config_manager.app_config.scheduler
                if not scheduler_config.type:
                    warnings.append("Scheduler type not specified in configuration")
            except Exception as e:
                errors.append(f"Configuration access failed: {e!s}")

        except Exception as e:
            errors.append(f"Validation failed: {e!s}")

        return ValidationResultDTO(
            is_valid=len(errors) == 0, validation_errors=errors, warnings=warnings
        )

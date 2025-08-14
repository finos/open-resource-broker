"""Scheduler query handlers for administrative operations."""

from typing import List

from src.application.base.handlers import BaseQueryHandler
from src.application.decorators import query_handler
from src.application.dto.system import (
    SchedulerConfigurationResponse,
    SchedulerStrategyDTO,
    SchedulerStrategyListResponse,
    ValidationResultDTO,
)
from src.application.queries.scheduler import (
    GetSchedulerConfigurationQuery,
    ListSchedulerStrategiesQuery,
    ValidateSchedulerConfigurationQuery,
)


@query_handler(ListSchedulerStrategiesQuery)
class ListSchedulerStrategiesHandler(
    BaseQueryHandler[ListSchedulerStrategiesQuery, SchedulerStrategyListResponse]
):
    """Handler for listing available scheduler strategies."""

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
        from src.config.manager import ConfigurationManager
        from src.infrastructure.registry.scheduler_registry import (
            get_scheduler_registry,
        )

        registry = get_scheduler_registry()
        scheduler_types = registry.get_registered_types()

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
                active=(scheduler_type ==
                        current_strategy if query.include_current else False),
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

        return SchedulerStrategyListResponse(
            strategies=strategies,
            current_strategy=current_strategy,
            total_count=len(strategies),
        )

    def _get_scheduler_description(self, scheduler_type: str) -> str:
        """Get description for scheduler type."""
        descriptions = {
            "default": "Default scheduler using native domain fields without conversion",
            "hostfactory": "Symphony HostFactory scheduler with field mapping and conversion",
            "hf": "Alias for Symphony HostFactory scheduler",
        }
        return descriptions.get(scheduler_type, f"Scheduler strategy: {scheduler_type}")

    def _get_scheduler_capabilities(self, scheduler_type: str) -> List[str]:
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
        from src.config.manager import ConfigurationManager
        from src.infrastructure.registry.scheduler_registry import (
            get_scheduler_registry,
        )

        config_manager = ConfigurationManager()
        registry = get_scheduler_registry()

        if query.scheduler_name:
            scheduler_name = query.scheduler_name
            is_active = scheduler_name == config_manager.get_scheduler_strategy()
        else:
            scheduler_name = config_manager.get_scheduler_strategy()
            is_active = True

        # Check if scheduler is registered
        registered_types = registry.get_registered_types()
        is_registered = scheduler_name in registered_types

        # Get configuration details
        configuration = {}
        found = False

        try:
            app_config = config_manager.get_app_config()
            if hasattr(app_config, "scheduler"):
                configuration = app_config.scheduler.model_dump()
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
        from src.config.manager import ConfigurationManager
        from src.infrastructure.registry.scheduler_registry import (
            get_scheduler_registry,
        )

        config_manager = ConfigurationManager()
        registry = get_scheduler_registry()

        errors = []
        warnings = []

        try:
            if query.scheduler_name:
                scheduler_name = query.scheduler_name
            else:
                scheduler_name = config_manager.get_scheduler_strategy()

            # Check if scheduler is registered
            registered_types = registry.get_registered_types()
            if scheduler_name not in registered_types:
                errors.append(
                    f"Scheduler '{scheduler_name}' is not registered. Available: {', '.join(registered_types)}"
                )

            # Try to create scheduler strategy
            try:
                strategy = registry.create_strategy(scheduler_name, config_manager)
                if strategy is None:
                    errors.append(
                        f"Failed to create scheduler strategy '{scheduler_name}'")
            except Exception as e:
                errors.append(f"Scheduler strategy creation failed: {str(e)}")

            # Check configuration completeness
            try:
                app_config = config_manager.get_app_config()
                if not hasattr(app_config, "scheduler"):
                    warnings.append(
                        "No scheduler configuration section found in config")
                elif not app_config.scheduler.type:
                    warnings.append("Scheduler type not specified in configuration")
            except Exception as e:
                errors.append(f"Configuration access failed: {str(e)}")

        except Exception as e:
            errors.append(f"Validation failed: {str(e)}")

        return ValidationResultDTO(
            is_valid=len(errors) == 0, validation_errors=errors, warnings=warnings
        )

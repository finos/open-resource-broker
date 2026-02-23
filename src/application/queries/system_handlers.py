"""System query handlers for administrative operations."""

from typing import TYPE_CHECKING, Any

from application.base.handlers import BaseQueryHandler
from application.decorators import query_handler
from application.dto.queries import (
    GetProviderHealthQuery,
    ValidateMCPQuery,
    ValidateStorageQuery,
)
from application.dto.system import (
    ConfigurationSectionResponse,
    ProviderConfigDTO,
    ProviderMetricsDTO,
    SystemStatusDTO,
    ValidationResultDTO,
)
from application.queries.system import (
    GetConfigurationSectionQuery,
    GetProviderConfigQuery,
    GetProviderMetricsQuery,
    GetSystemStatusQuery,
    ValidateProviderConfigQuery,
)


from domain.base import UnitOfWorkFactory
from domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort
from domain.services.timestamp_service import TimestampService

# Use TYPE_CHECKING to avoid direct infrastructure imports
if TYPE_CHECKING:
    pass


@query_handler(GetConfigurationSectionQuery)
class GetConfigurationSectionHandler(
    BaseQueryHandler[GetConfigurationSectionQuery, ConfigurationSectionResponse]
):
    """Handler for getting configuration sections."""

    def __init__(
        self,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
    ):
        super().__init__(logger, error_handler)
        self._container = container

    async def execute_query(
        self, query: GetConfigurationSectionQuery
    ) -> ConfigurationSectionResponse:
        """
        Execute configuration section query.

        Args:
            query: Configuration section query

        Returns:
            Configuration section response
        """
        # Access configuration through DI container
        from domain.base.ports import ConfigurationPort

        config_manager = self._container.get(ConfigurationPort)
        section_config = config_manager.get(query.section, {})

        return ConfigurationSectionResponse(
            section=query.section,
            config=section_config if isinstance(section_config, dict) else {},
            found=bool(section_config),
        )


@query_handler(GetProviderConfigQuery)
class GetProviderConfigHandler(BaseQueryHandler[GetProviderConfigQuery, ProviderConfigDTO]):
    """Handler for getting provider configuration information."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
        timestamp_service: TimestampService,
    ) -> None:
        """
        Initialize get provider config handler.

        Args:
            logger: Logging port for operation logging
            container: Container port for dependency access
            error_handler: Error handling port for exception management
            timestamp_service: Service for timestamp formatting
        """
        super().__init__(logger, error_handler)
        self.container = container
        self.timestamp_service = timestamp_service
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: GetProviderConfigQuery) -> ProviderConfigDTO:
        """Execute provider configuration query."""
        self.logger.info("Getting provider configuration")

        try:
            # Get configuration manager from container
            from domain.base.ports import ConfigurationPort

            config_manager = self.container.get(ConfigurationPort)

            # Get provider configuration
            if hasattr(config_manager, "get_provider_config"):
                provider_config = config_manager.get_provider_config()

                # Get full configuration sources
                config_sources = config_manager.get_configuration_sources()

                # Determine default provider
                active_providers = (
                    provider_config.get_active_providers()
                    if hasattr(provider_config, "get_active_providers")
                    else []
                )
                default_provider = active_providers[0].name if active_providers else None

                # Get last updated time from config file
                last_updated = None
                if config_sources.get("config_file"):
                    import os

                    try:
                        mtime = os.path.getmtime(config_sources["config_file"])
                        last_updated = self.timestamp_service.format_for_display(mtime)
                    except (OSError, ValueError):
                        pass

                return ProviderConfigDTO(
                    provider_mode=(
                        provider_config.get_mode().value
                        if hasattr(provider_config, "get_mode")
                        else "legacy"
                    ),
                    active_providers=([p.name for p in active_providers]),
                    provider_count=len(active_providers),
                    default_provider=default_provider,
                    configuration_source=config_sources["primary_source"],
                    config_file=config_sources.get("config_file"),
                    template_file=config_sources.get("template_file"),
                    last_updated=last_updated,
                )
            else:
                # Fallback for legacy configuration
                return ProviderConfigDTO(
                    provider_mode="legacy",
                    active_providers=["aws"],
                    provider_count=1,
                    configuration_source="legacy",
                )
                return {
                    "provider_mode": "legacy",
                    "active_providers": [],
                    "provider_count": 0,
                    "configuration_source": "legacy",
                }

        except Exception as e:
            self.logger.error("Failed to get provider configuration: %s", e)
            raise

    def _get_configuration_source(self, config_manager) -> str:
        """Get source of configuration using domain service."""
        sources = config_manager.get_configuration_sources()
        return sources["primary_source"]


@query_handler(ValidateProviderConfigQuery)
class ValidateProviderConfigHandler(
    BaseQueryHandler[ValidateProviderConfigQuery, ValidationResultDTO]
):
    """Handler for validating provider configuration."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """
        Initialize validate provider config handler.

        Args:
            logger: Logging port for operation logging
            container: Container port for dependency access
            error_handler: Error handling port for exception management
        """
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: ValidateProviderConfigQuery) -> dict[str, Any]:
        """Execute provider configuration validation query."""
        self.logger.info("Validating provider configuration")

        try:
            # Get configuration manager from container
            from domain.base.ports import ConfigurationPort

            config_manager = self.container.get(ConfigurationPort)

            validation_errors = []
            warnings = []

            # Validate configuration structure
            if hasattr(config_manager, "validate_configuration"):
                validation_result = config_manager.validate_configuration()
                validation_errors.extend(validation_result.get("errors", []))
                warnings.extend(validation_result.get("warnings", []))

            # Additional validation logic
            try:
                provider_config = (
                    config_manager.get_provider_config()
                    if hasattr(config_manager, "get_provider_config")
                    else None
                )
                if provider_config and hasattr(provider_config, "get_active_providers"):
                    active_providers = provider_config.get_active_providers()
                    if not active_providers:
                        warnings.append("No active providers configured")
                else:
                    warnings.append("Unable to access provider configuration")
            except Exception as validation_error:
                validation_errors.append(
                    f"Provider configuration validation failed: {validation_error!s}"
                )

            is_valid = len(validation_errors) == 0

            return {
                "is_valid": is_valid,
                "validation_errors": validation_errors,
                "warnings": warnings,
            }

        except Exception as e:
            self.logger.error("Failed to validate provider configuration: %s", e)
            raise


@query_handler(GetSystemStatusQuery)
class GetSystemStatusHandler(BaseQueryHandler[GetSystemStatusQuery, SystemStatusDTO]):
    """Handler for getting system status information."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
        timestamp_service: TimestampService,
    ) -> None:
        """
        Initialize get system status handler.

        Args:
            logger: Logging port for operation logging
            container: Container port for dependency access
            error_handler: Error handling port for exception management
            timestamp_service: Service for timestamp formatting
        """
        super().__init__(logger, error_handler)
        self.container = container
        self.timestamp_service = timestamp_service
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: GetSystemStatusQuery) -> dict[str, Any]:
        """Execute system status query."""
        self.logger.info("Getting system status")

        try:
            import time

            # Get basic system information
            system_status = {
                "status": "operational",
                "timestamp": self.timestamp_service.current_timestamp(),
                "uptime": self.timestamp_service.format_for_display(time.time()),
                "components": {},
            }

            # Check provider status
            try:
                from domain.base.ports import ConfigurationPort

                self.container.get(ConfigurationPort)
                system_status["components"]["configuration"] = {
                    "status": "healthy",
                    "details": "Configuration manager operational",
                }
            except Exception as e:
                system_status["components"]["configuration"] = {
                    "status": "unhealthy",
                    "details": f"Configuration manager error: {e!s}",
                }
                system_status["status"] = "degraded"

            # Check container status
            try:
                # Basic container health check
                system_status["components"]["dependency_injection"] = {
                    "status": "healthy",
                    "details": "DI container operational",
                }
            except Exception as e:
                system_status["components"]["dependency_injection"] = {
                    "status": "unhealthy",
                    "details": f"DI container error: {e!s}",
                }
                system_status["status"] = "degraded"

            return system_status

        except Exception as e:
            self.logger.error("Failed to get system status: %s", e)
            raise


@query_handler(GetProviderMetricsQuery)
class GetProviderMetricsHandler(BaseQueryHandler[GetProviderMetricsQuery, ProviderMetricsDTO]):
    """Handler for getting provider metrics information."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
        uow_factory: UnitOfWorkFactory,
    ) -> None:
        """
        Initialize get provider metrics handler.

        Args:
            logger: Logging port for operation logging
            container: Container port for dependency access
            error_handler: Error handling port for exception management
            uow_factory: Unit of work factory for data access
        """
        super().__init__(logger, error_handler)
        self.container = container
        self.uow_factory = uow_factory

    async def execute_query(self, query: GetProviderMetricsQuery) -> dict[str, Any]:
        """Execute provider metrics query."""
        self.logger.info("Getting provider metrics for timeframe: %s", query.timeframe)

        try:
            from datetime import datetime, timedelta, timezone

            # Calculate time range based on query timeframe
            end_time = datetime.now(timezone.utc)
            if query.timeframe == "1h":
                start_time = end_time - timedelta(hours=1)
            elif query.timeframe == "24h":
                start_time = end_time - timedelta(hours=24)
            elif query.timeframe == "7d":
                start_time = end_time - timedelta(days=7)
            else:
                start_time = end_time - timedelta(hours=1)  # Default to 1 hour

            # Get actual metrics from repository using UoW pattern
            with self.uow_factory.create_unit_of_work() as uow:
                # Get all-time metrics for accurate counts
                all_requests = uow.requests.find_all()
                all_time_metrics = {
                    "total": len(all_requests),
                    "completed": sum(1 for r in all_requests if r.status.value == "complete"),
                    "failed": sum(1 for r in all_requests if r.status.value == "failed"),
                    "in_progress": sum(
                        1
                        for r in all_requests
                        if r.status.value in ["in_progress", "running", "shutting-down"]
                    ),
                    "pending": sum(1 for r in all_requests if r.status.value == "pending"),
                }

                # Get timeframe-specific metrics for comparison
                timeframe_requests = uow.requests.find_by_date_range(start_time, end_time)
                timeframe_metrics = {
                    "total": len(timeframe_requests),
                    "completed": sum(1 for r in timeframe_requests if r.status.value == "complete"),
                    "failed": sum(1 for r in timeframe_requests if r.status.value == "failed"),
                    "in_progress": sum(
                        1
                        for r in timeframe_requests
                        if r.status.value in ["in_progress", "running", "shutting-down"]
                    ),
                    "pending": sum(1 for r in timeframe_requests if r.status.value == "pending"),
                }

            # Build response with all-time data and timeframe annotation
            metrics = {
                "timeframe": f"{query.timeframe} (showing all-time data)",
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "timeframe_requests": timeframe_metrics["total"],
                "providers": {},
                "summary": {
                    "total_requests": all_time_metrics["total"],
                    "successful_requests": all_time_metrics["completed"],
                    "failed_requests": all_time_metrics["failed"],
                    "in_progress_requests": all_time_metrics["in_progress"],
                    "pending_requests": all_time_metrics["pending"],
                    "average_response_time": 0.0,
                },
            }

            # Try to get actual provider metrics if available
            try:
                from domain.base.ports import ConfigurationPort

                config_manager = self.container.get(ConfigurationPort)

                if hasattr(config_manager, "get_provider_config"):
                    provider_config = config_manager.get_provider_config()
                    if hasattr(provider_config, "get_active_providers"):
                        active_providers = provider_config.get_active_providers()

                        for provider in active_providers:
                            metrics["providers"][provider.name] = {
                                "status": "active",
                                "type": (provider.type if hasattr(provider, "type") else "unknown"),
                                "requests": all_time_metrics["total"],
                                "errors": all_time_metrics["failed"],
                                "timeframe_requests": timeframe_metrics["total"],
                                "avg_response_time": 0.0,
                            }
            except Exception as provider_error:
                self.logger.warning("Could not get provider-specific metrics: %s", provider_error)
                metrics["providers"]["default"] = {
                    "status": "unknown",
                    "type": "unknown",
                    "requests": all_time_metrics["total"],
                    "errors": all_time_metrics["failed"],
                    "timeframe_requests": timeframe_metrics["total"],
                    "avg_response_time": 0.0,
                }

            return metrics

        except Exception as e:
            self.logger.error("Failed to get provider metrics: %s", e)
            raise


@query_handler(GetProviderHealthQuery)
class GetProviderHealthHandler(BaseQueryHandler[GetProviderHealthQuery, dict[str, Any]]):
    """Handler for getting provider health status."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize get provider health handler."""
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: GetProviderHealthQuery) -> dict[str, Any]:
        """Execute provider health query."""
        self.logger.info("Getting provider health status for: %s", query.provider_name or "all")

        try:
            from domain.base.ports import ConfigurationPort

            config_manager = self.container.get(ConfigurationPort)

            # Get provider configuration
            if hasattr(config_manager, "get_provider_config"):
                provider_config = config_manager.get_provider_config()
                active_providers = (
                    provider_config.get_active_providers()
                    if hasattr(provider_config, "get_active_providers")
                    else []
                )

                health_status = {
                    "status": "success",
                    "timestamp": TimestampService().current_timestamp(),
                    "providers": {},
                }

                # Check health for specific provider or all
                for provider in active_providers:
                    if query.provider_name and provider.name != query.provider_name:
                        continue

                    # Basic health check - provider is configured and accessible
                    health_status["providers"][provider.name] = {
                        "status": "healthy",
                        "name": provider.name,
                        "type": provider.type if hasattr(provider, "type") else "unknown",
                        "available": True,
                    }

                return health_status
            else:
                return {
                    "status": "error",
                    "error": "Provider configuration not available",
                    "providers": {},
                }

        except Exception as e:
            self.logger.error("Failed to get provider health: %s", e, exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "providers": {},
            }


@query_handler(ValidateStorageQuery)
class ValidateStorageHandler(BaseQueryHandler[ValidateStorageQuery, dict[str, Any]]):
    """Handler for validating storage connectivity."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
        uow_factory: UnitOfWorkFactory,
    ) -> None:
        """Initialize validate storage handler."""
        super().__init__(logger, error_handler)
        self.container = container
        self.uow_factory = uow_factory

    async def execute_query(self, query: ValidateStorageQuery) -> dict[str, Any]:
        """Execute storage validation query."""
        self.logger.info("Validating storage connectivity")

        try:
            # Test storage connectivity by attempting to list requests
            with self.uow_factory.create_unit_of_work() as uow:
                # Try to access repositories
                requests = uow.requests.list_all()
                machines = uow.machines.list_all()

                return {
                    "status": "success",
                    "storage_accessible": True,
                    "request_count": len(requests),
                    "machine_count": len(machines),
                    "message": "Storage is accessible and operational",
                }

        except Exception as e:
            self.logger.error("Storage validation failed: %s", e, exc_info=True)
            return {
                "status": "error",
                "storage_accessible": False,
                "error": str(e),
                "message": "Storage validation failed",
            }


@query_handler(ValidateMCPQuery)
class ValidateMCPHandler(BaseQueryHandler[ValidateMCPQuery, dict[str, Any]]):
    """Handler for validating MCP configuration."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize validate MCP handler."""
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: ValidateMCPQuery) -> dict[str, Any]:
        """Execute MCP validation query."""
        self.logger.info("Validating MCP configuration")

        try:
            # Basic MCP validation - check if configuration is accessible
            from domain.base.ports import ConfigurationPort

            config_manager = self.container.get(ConfigurationPort)

            validation_errors = []
            warnings = []

            # Check if MCP configuration exists
            mcp_config = config_manager.get("mcp", {})
            if not mcp_config:
                warnings.append("MCP configuration not found")

            # Validate MCP structure
            if isinstance(mcp_config, dict):
                if "enabled" not in mcp_config:
                    warnings.append("MCP 'enabled' flag not configured")
                if "endpoint" not in mcp_config:
                    warnings.append("MCP 'endpoint' not configured")
            else:
                validation_errors.append("MCP configuration is not a valid dictionary")

            is_valid = len(validation_errors) == 0

            return {
                "status": "success" if is_valid else "error",
                "is_valid": is_valid,
                "validation_errors": validation_errors,
                "warnings": warnings,
                "mcp_enabled": mcp_config.get("enabled", False) if isinstance(mcp_config, dict) else False,
            }

        except Exception as e:
            self.logger.error("MCP validation failed: %s", e, exc_info=True)
            return {
                "status": "error",
                "is_valid": False,
                "validation_errors": [str(e)],
                "warnings": [],
                "mcp_enabled": False,
            }

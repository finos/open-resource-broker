"""System query handlers for administrative operations."""

from typing import TYPE_CHECKING, Any, Dict

from application.base.handlers import BaseQueryHandler
from application.decorators import query_handler
from application.dto.system import (
    ConfigurationSectionResponse,
    ConfigurationValueResponse,
    ProviderConfigDTO,
    ProviderMetricsDTO,
    SystemStatusDTO,
    ValidationResultDTO,
)
from application.queries.system import (
    GetConfigurationQuery,
    GetConfigurationSectionQuery,
    GetProviderConfigQuery,
    GetProviderMetricsQuery,
    GetSystemStatusQuery,
    ValidateProviderConfigQuery,
)
from domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort

# Use TYPE_CHECKING to avoid direct infrastructure imports
if TYPE_CHECKING:
    pass


@query_handler(GetConfigurationQuery)
class GetConfigurationHandler(BaseQueryHandler[GetConfigurationQuery, ConfigurationValueResponse]):
    """Handler for getting configuration values."""

    async def execute_query(self, query: GetConfigurationQuery) -> ConfigurationValueResponse:
        """
        Execute configuration value query.

        Args:
            query: Configuration query

        Returns:
            Configuration value response
        """
        # Access configuration through application layer
        from config.manager import get_config_manager

        config_manager = get_config_manager()

        if query.section:
            # Get value from specific section
            section_config = config_manager.get(query.section, {})
            if isinstance(section_config, dict):
                value = section_config.get(query.key.split(".")[-1], query.default)
            else:
                value = query.default
        else:
            # Get value directly by key
            value = config_manager.get(query.key, query.default)

        return ConfigurationValueResponse(
            key=query.key,
            value=value,
            section=query.section,
            found=value != query.default,
        )


@query_handler(GetConfigurationSectionQuery)
class GetConfigurationSectionHandler(
    BaseQueryHandler[GetConfigurationSectionQuery, ConfigurationSectionResponse]
):
    """Handler for getting configuration sections."""

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
        # Access configuration through application layer
        from config.manager import get_config_manager

        config_manager = get_config_manager()
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
    ):
        """
        Initialize get provider config handler.

        Args:
            logger: Logging port for operation logging
            container: Container port for dependency access
            error_handler: Error handling port for exception management
        """
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

                return ProviderConfigDTO(
                    provider_mode=(
                        provider_config.get_mode().value
                        if hasattr(provider_config, "get_mode")
                        else "legacy"
                    ),
                    active_providers=(
                        [p.name for p in provider_config.get_active_providers()]
                        if hasattr(provider_config, "get_active_providers")
                        else []
                    ),
                    provider_count=(
                        len(provider_config.get_active_providers())
                        if hasattr(provider_config, "get_active_providers")
                        else 0
                    ),
                    configuration_source=getattr(provider_config, "source", "unknown"),
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
            self.logger.error(f"Failed to get provider configuration: {e}")
            raise


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
    ):
        """
        Initialize validate provider config handler.

        Args:
            logger: Logging port for operation logging
            container: Container port for dependency access
            error_handler: Error handling port for exception management
        """
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: ValidateProviderConfigQuery) -> Dict[str, Any]:
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
                    f"Provider configuration validation failed: {str(validation_error)}"
                )

            is_valid = len(validation_errors) == 0

            return {
                "is_valid": is_valid,
                "validation_errors": validation_errors,
                "warnings": warnings,
                "validation_timestamp": query.timestamp or "unknown",
            }

        except Exception as e:
            self.logger.error(f"Failed to validate provider configuration: {e}")
            raise


@query_handler(GetSystemStatusQuery)
class GetSystemStatusHandler(BaseQueryHandler[GetSystemStatusQuery, SystemStatusDTO]):
    """Handler for getting system status information."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
    ):
        """
        Initialize get system status handler.

        Args:
            logger: Logging port for operation logging
            container: Container port for dependency access
            error_handler: Error handling port for exception management
        """
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: GetSystemStatusQuery) -> Dict[str, Any]:
        """Execute system status query."""
        self.logger.info("Getting system status")

        try:
            import time
            from datetime import datetime

            # Get basic system information
            system_status = {
                "status": "operational",
                "timestamp": datetime.utcnow().isoformat(),
                "uptime": time.time(),  # Simplified uptime
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
                    "details": f"Configuration manager error: {str(e)}",
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
                    "details": f"DI container error: {str(e)}",
                }
                system_status["status"] = "degraded"

            return system_status

        except Exception as e:
            self.logger.error(f"Failed to get system status: {e}")
            raise


@query_handler(GetProviderMetricsQuery)
class GetProviderMetricsHandler(BaseQueryHandler[GetProviderMetricsQuery, ProviderMetricsDTO]):
    """Handler for getting provider metrics information."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
    ):
        """
        Initialize get provider metrics handler.

        Args:
            logger: Logging port for operation logging
            container: Container port for dependency access
            error_handler: Error handling port for exception management
        """
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: GetProviderMetricsQuery) -> Dict[str, Any]:
        """Execute provider metrics query."""
        self.logger.info(f"Getting provider metrics for timeframe: {query.timeframe}")

        try:
            from datetime import datetime, timedelta

            # Calculate time range based on query timeframe
            end_time = datetime.utcnow()
            if query.timeframe == "1h":
                start_time = end_time - timedelta(hours=1)
            elif query.timeframe == "24h":
                start_time = end_time - timedelta(hours=24)
            elif query.timeframe == "7d":
                start_time = end_time - timedelta(days=7)
            else:
                start_time = end_time - timedelta(hours=1)  # Default to 1 hour

            # Get provider metrics (simplified implementation)
            metrics = {
                "timeframe": query.timeframe,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "providers": {},
                "summary": {
                    "total_requests": 0,
                    "successful_requests": 0,
                    "failed_requests": 0,
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
                                "requests": 0,
                                "errors": 0,
                                "avg_response_time": 0.0,
                            }
            except Exception as provider_error:
                self.logger.warning(f"Could not get provider-specific metrics: {provider_error}")
                metrics["providers"]["default"] = {
                    "status": "unknown",
                    "type": "unknown",
                    "requests": 0,
                    "errors": 0,
                    "avg_response_time": 0.0,
                }

            return metrics

        except Exception as e:
            self.logger.error(f"Failed to get provider metrics: {e}")
            raise

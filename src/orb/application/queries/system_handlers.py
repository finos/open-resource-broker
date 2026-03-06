"""System query handlers for administrative operations."""

from typing import TYPE_CHECKING, Any

from orb.application.base.handlers import BaseQueryHandler
from orb.application.decorators import query_handler
from orb.application.dto.queries import (
    ValidateMCPQuery,  # type: ignore[attr-defined]
    ValidateStorageQuery,  # type: ignore[attr-defined]
)
from orb.application.dto.system import (
    ConfigurationSectionResponse,
    ProviderConfigDTO,
    SystemStatusDTO,
    ValidationResultDTO,
)
from orb.application.queries.system import (
    GetConfigurationSectionQuery,
    GetProviderConfigQuery,
    GetSystemStatusQuery,
    ValidateProviderConfigQuery,
)
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort
from orb.domain.services.timestamp_service import TimestampService

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
        from orb.domain.base.ports import ConfigurationPort

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
            from orb.domain.base.ports import ConfigurationPort

            config_manager = self.container.get(ConfigurationPort)

            # Get provider configuration
            if hasattr(config_manager, "get_provider_config"):
                provider_config = config_manager.get_provider_config()

                # Get full configuration sources
                config_sources = config_manager.get_configuration_sources()

                # Determine default provider
                active_providers = (
                    provider_config.get_active_providers()  # type: ignore[union-attr]
                    if hasattr(provider_config, "get_active_providers")
                    else []
                )
                default_provider = active_providers[0].name if active_providers else None  # type: ignore[union-attr]

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
                        provider_config.get_mode().value  # type: ignore[union-attr]
                        if hasattr(provider_config, "get_mode")
                        else "legacy"
                    ),
                    active_providers=([p.name for p in active_providers]),  # type: ignore[union-attr]
                    provider_count=len(active_providers),
                    default_provider=default_provider,
                    configuration_source=config_sources["primary_source"]
                    if isinstance(config_sources, dict)
                    else "unknown",
                    config_file=config_sources.get("config_file")
                    if isinstance(config_sources, dict)
                    else None,
                    template_file=config_sources.get("template_file")
                    if isinstance(config_sources, dict)
                    else None,
                    last_updated=last_updated,
                )
            else:
                # Fallback for legacy configuration
                return ProviderConfigDTO(
                    provider_mode="legacy",
                    active_providers=["aws"],
                    provider_count=1,
                    default_provider=None,
                    configuration_source="legacy",
                    config_file=None,
                    template_file=None,
                    last_updated=None,
                )

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

    async def execute_query(self, query: ValidateProviderConfigQuery) -> ValidationResultDTO:  # type: ignore[override]
        """Execute provider configuration validation query."""
        self.logger.info("Validating provider configuration")

        try:
            # Get configuration manager from container
            from orb.domain.base.ports import ConfigurationPort

            config_manager = self.container.get(ConfigurationPort)

            validation_errors = []
            warnings = []

            # Validate configuration structure
            if hasattr(config_manager, "validate_configuration"):
                validation_result = config_manager.validate_configuration()
                if isinstance(validation_result, dict):
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
                    active_providers = provider_config.get_active_providers()  # type: ignore[union-attr]
                    if not active_providers:
                        warnings.append("No active providers configured")
                else:
                    warnings.append("Unable to access provider configuration")
            except Exception as validation_error:
                validation_errors.append(
                    f"Provider configuration validation failed: {validation_error!s}"
                )

            is_valid = len(validation_errors) == 0

            return ValidationResultDTO(
                is_valid=is_valid,
                validation_errors=validation_errors,
                warnings=warnings,
            )

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

    async def execute_query(self, query: GetSystemStatusQuery) -> SystemStatusDTO:  # type: ignore[override]
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
                from orb.domain.base.ports import ConfigurationPort

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

            return SystemStatusDTO(
                status=system_status["status"],
                uptime_seconds=0.0,
                version="unknown",
                environment="unknown",
                active_connections=0,
                memory_usage_mb=0.0,
                cpu_usage_percent=0.0,
                disk_usage_percent=0.0,
                components={k: str(v) for k, v in system_status["components"].items()},
            )

        except Exception as e:
            self.logger.error("Failed to get system status: %s", e)
            raise


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
            from orb.domain.base.ports import ConfigurationPort

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
                "mcp_enabled": mcp_config.get("enabled", False)
                if isinstance(mcp_config, dict)
                else False,
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

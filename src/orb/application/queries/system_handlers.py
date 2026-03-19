"""System query handlers for administrative operations."""

from typing import TYPE_CHECKING, Any, cast

from orb.application.base.handlers import BaseQueryHandler
from orb.application.decorators import query_handler
from orb.application.dto.queries import (
    ValidateMCPQuery,  # type: ignore[attr-defined]
    ValidateStorageQuery,  # type: ignore[attr-defined]
)
from orb.application.dto.system import (
    CircuitBreakerSectionDTO,
    ConfigurationSectionResponse,
    LoggingSectionDTO,
    PathsSectionDTO,
    ProviderConfigDTO,
    ProviderSectionDTO,
    RequestLimitsSectionDTO,
    SchedulerSectionDTO,
    ServerSectionDTO,
    StorageSectionDTO,
    SystemConfigDTO,
    SystemStatusDTO,
    ValidationResultDTO,
)
from orb.application.queries.system import (
    GetConfigurationSectionQuery,
    GetProviderConfigQuery,
    GetSystemConfigQuery,
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
            provider_config = config_manager.get_provider_config()

            # Get full configuration sources
            config_sources = config_manager.get_configuration_sources()

            # Determine default provider
            active_providers = (
                provider_config.get_active_providers()  # type: ignore[union-attr]
                if provider_config is not None
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
                except (OSError, ValueError) as exc:
                    # Failure to read or format the config file's modification time is non-fatal;
                    # log at debug level and leave `last_updated` as None.
                    self.logger.debug(
                        "Failed to determine last updated time for config file '%s': %s",
                        config_sources.get("config_file"),
                        exc,
                    )

            return ProviderConfigDTO(
                provider_mode=(
                    provider_config.get_mode().value  # type: ignore[union-attr]
                    if provider_config is not None
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
            validation_result = config_manager.validate_configuration()
            if isinstance(validation_result, dict):
                validation_errors.extend(validation_result.get("errors", []))
                warnings.extend(validation_result.get("warnings", []))

            # Additional validation logic
            try:
                provider_config = config_manager.get_provider_config()
                if provider_config is not None:
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
            import importlib.metadata
            import os
            import time

            import psutil

            # Real metrics
            uptime_seconds = time.time() - psutil.boot_time()
            memory_usage_mb = psutil.Process().memory_info().rss / 1024 / 1024
            cpu_usage_percent = psutil.cpu_percent(interval=None)
            disk_usage_percent = psutil.disk_usage("/").percent

            try:
                version = importlib.metadata.version("orb")
            except importlib.metadata.PackageNotFoundError:
                version = "unknown"

            environment = os.environ.get("ORB_ENVIRONMENT", os.environ.get("ENV", "production"))

            system_status: dict[str, Any] = {
                "status": "operational",
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
                uptime_seconds=uptime_seconds,
                version=version,
                environment=environment,
                active_connections=0,
                memory_usage_mb=memory_usage_mb,
                cpu_usage_percent=cpu_usage_percent,
                disk_usage_percent=disk_usage_percent,
                components=system_status["components"],
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


@query_handler(GetSystemConfigQuery)
class GetSystemConfigHandler(BaseQueryHandler[GetSystemConfigQuery, SystemConfigDTO]):
    """Handler for getting full system configuration overview."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.container = container

    async def execute_query(self, query: GetSystemConfigQuery) -> SystemConfigDTO:
        """Execute system config query."""
        from orb.domain.base.ports import ConfigurationPort

        cfg = self.container.get(ConfigurationPort)

        # --- paths ---
        loaded_templates_file: str | None = None
        template_search_paths: list[str] | None = None
        try:
            import os

            from orb.application.ports.scheduler_port import SchedulerPort

            scheduler = self.container.get(SchedulerPort)
            all_paths = scheduler.get_template_paths() or []

            for path in all_paths:
                if os.path.exists(path):
                    loaded_templates_file = path
                    break

            if query.verbose:
                template_search_paths = all_paths
        except Exception:  # noqa: BLE001 — template path resolution is best-effort; fall back to None
            pass

        paths = PathsSectionDTO(
            root_dir=cfg.get_root_dir() if hasattr(cfg, "get_root_dir") else "",
            config_dir=cfg.get_config_dir() if hasattr(cfg, "get_config_dir") else "",
            work_dir=cfg.get_work_dir() if hasattr(cfg, "get_work_dir") else "",
            log_dir=cfg.get_log_dir() if hasattr(cfg, "get_log_dir") else "",
            loaded_config_file=cast(Any, cfg).get_loaded_config_file()
            if hasattr(cfg, "get_loaded_config_file")
            else None,
            loaded_templates_file=loaded_templates_file,
            template_search_paths=template_search_paths,
        )

        # --- provider ---
        active_providers: list[str] = []
        provider_mode = "legacy"
        default_provider: str | None = None
        try:
            provider_config = (
                cfg.get_provider_config() if hasattr(cfg, "get_provider_config") else None
            )
            if provider_config and hasattr(provider_config, "get_active_providers"):
                providers = provider_config.get_active_providers()
                active_providers = [p.name for p in providers]
                default_provider = active_providers[0] if active_providers else None
            if provider_config and hasattr(provider_config, "get_mode"):
                provider_mode = provider_config.get_mode().value
        except Exception as exc:
            # Provider config inspection failed — fall back to defaults
            self.logger.warning("Could not inspect provider configuration: %s", exc)
        provider = ProviderSectionDTO(
            active_providers=active_providers,
            provider_mode=provider_mode,
            default_provider=default_provider,
        )

        # --- storage ---
        storage_strategy = cfg.get_storage_strategy()
        storage_cfg = cfg.get_storage_config() if hasattr(cfg, "get_storage_config") else {}
        storage = StorageSectionDTO(
            strategy=storage_strategy,
            data_path=storage_cfg.get("data_path") if isinstance(storage_cfg, dict) else None,
            backup_enabled=bool(storage_cfg.get("backup_enabled", False))
            if isinstance(storage_cfg, dict)
            else False,
            backup_path=storage_cfg.get("backup_path") if isinstance(storage_cfg, dict) else None,
        )

        # --- scheduler ---
        scheduler = SchedulerSectionDTO(scheduler_type=cfg.get_scheduler_strategy())

        # --- server (only if enabled) ---
        server: ServerSectionDTO | None = None
        server_cfg = cfg.get("server", {})
        if isinstance(server_cfg, dict) and server_cfg.get("enabled"):
            server = ServerSectionDTO(
                enabled=True,
                host=server_cfg.get("host"),
                port=server_cfg.get("port"),
            )

        # --- verbose sections ---
        logging_section: LoggingSectionDTO | None = None
        request_limits: RequestLimitsSectionDTO | None = None
        circuit_breaker: CircuitBreakerSectionDTO | None = None

        if query.verbose:
            log_cfg = cfg.get_logging_config() if hasattr(cfg, "get_logging_config") else {}
            if isinstance(log_cfg, dict):
                logging_section = LoggingSectionDTO(
                    level=str(log_cfg.get("level", "INFO")),
                    log_file=log_cfg.get("file"),
                    console_enabled=bool(log_cfg.get("console_enabled", True)),
                )

            req_cfg = cfg.get_request_config() if hasattr(cfg, "get_request_config") else {}
            if isinstance(req_cfg, dict):
                request_limits = RequestLimitsSectionDTO(
                    max_machines=req_cfg.get("max_machines_per_request"),
                    default_timeout=req_cfg.get("default_timeout"),
                    grace_period=req_cfg.get("grace_period"),
                )

            cb_cfg = cfg.get("circuit_breaker", {})
            if isinstance(cb_cfg, dict):
                circuit_breaker = CircuitBreakerSectionDTO(
                    enabled=bool(cb_cfg.get("enabled", False)),
                    failure_threshold=cb_cfg.get("failure_threshold"),
                    recovery_timeout=cb_cfg.get("recovery_timeout"),
                )

        return SystemConfigDTO(
            paths=paths,
            provider=provider,
            storage=storage,
            scheduler=scheduler,
            server=server,
            logging=logging_section,
            request_limits=request_limits,
            circuit_breaker=circuit_breaker,
        )

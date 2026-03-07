"""Application bootstrap - DI-based architecture."""

from __future__ import annotations

from typing import Any, Optional

# Import configuration
from orb.config.schemas.app_schema import AppConfig

# Import logging
from orb.infrastructure.logging.logger import get_logger, setup_logging

# Import DI container


class Application:
    """DI-based application context manager with registration pattern."""

    def __init__(
        self,
        config_path: Optional[str] = None,
        config_dict: Optional[dict[str, Any]] = None,
        skip_validation: bool = False,
        container: Optional[Any] = None,
    ) -> None:
        """Initialize the instance."""
        self.config_path = config_path
        self.config_dict = config_dict
        self._initialized = False
        self._external_container = container

        # Skip validation for commands that don't need it (templates, init, help)
        if not skip_validation:
            from orb.infrastructure.validation.startup_validator import StartupValidator

            validator = StartupValidator(config_path)
            validator.validate_startup()

        # Defer heavy initialization until first use
        self._container: Any = None
        self._config_manager: Any = None
        self._domain_container_set = False
        self.provider_type = None
        self._dry_run_context = None

        # Only create logger immediately (lightweight)
        self.logger = get_logger(__name__)

    def _ensure_container(self) -> None:
        """Ensure DI container is created (lazy initialization)."""
        if self._container is None:
            if self._external_container is not None:
                self._container = self._external_container
            else:
                from orb.infrastructure.di.container import get_container

                self._container = get_container()

            # Pre-register ConfigurationManager with config_dict if provided,
            # so DI container uses in-memory config instead of file discovery.
            if self.config_dict is not None:
                from orb.config.managers.configuration_manager import ConfigurationManager

                cm = ConfigurationManager(
                    config_file=self.config_path, config_dict=self.config_dict
                )
                self._container.register_instance(ConfigurationManager, cm)

            # Set up domain container for decorators
            if not self._domain_container_set:
                from orb.domain.base.decorators import set_domain_container

                set_domain_container(self._container)
                self._domain_container_set = True

    def _ensure_config_manager(self) -> None:
        """Ensure config manager is created (lazy initialization)."""
        if self._config_manager is None:
            if not self._container:
                raise RuntimeError("Application not initialized - call initialize() first")

            from orb.domain.base.ports.configuration_port import ConfigurationPort

            self._config_manager = self._container.get(ConfigurationPort)

            # Extract provider type from config
            provider_config = self._config_manager.get("provider", {"type": "mock"})
            if isinstance(provider_config, dict):
                self.provider_type = provider_config.get("type", "mock")
            else:
                self.provider_type = str(provider_config)

    def config_manager(self):
        """Get the configuration manager."""
        self._ensure_config_manager()
        return self._config_manager

    async def initialize(self, dry_run: bool = False) -> bool:
        """Initialize the application with DI container."""
        try:
            # Ensure container is available first (services already registered in get_container)
            self._ensure_container()

            # Now we can ensure config manager is available
            self._ensure_config_manager()

            self.logger.info("Initializing application with provider: %s", self.provider_type)

            # Log provider configuration information
            self._log_provider_configuration(self._config_manager)

            # Setup logging
            app_config = self._config_manager.get_typed(AppConfig)
            setup_logging(app_config.logging)

            # Activate dry-run context if requested
            if dry_run:
                from orb.infrastructure.mocking.dry_run_context import dry_run_context

                self.logger.info("DRY-RUN mode activated during application initialization")
                self._dry_run_context = dry_run_context(True)
                self._dry_run_context.__enter__()

            # Initialize provider registry directly
            from orb.providers.registry import get_provider_registry

            self._provider_registry = get_provider_registry()

            # Initialize provider registry based on loading mode
            if not self._container.is_lazy_loading_enabled():
                # Eager loading - ensure providers are registered
                self.logger.info("Eager loading - registering providers immediately")
                self._register_configured_providers()
            else:
                # Lazy loading - still need to register providers for discovery
                self.logger.info("Lazy loading enabled - registering providers for discovery")
                self._register_configured_providers()

            # Pre-load templates into cache during initialization
            await self._preload_templates()

            # Log final provider information
            self._log_final_provider_info()

            self._initialized = True
            self.logger.info(
                "Open Resource Broker initialized successfully with %s provider",
                self.provider_type,
            )
            return True

        except Exception as e:
            self.logger.error("Failed to initialize application: %s", e, exc_info=True)
            return False

    def _register_configured_providers(self) -> None:
        """Register providers from configuration with registry."""
        try:
            provider_config = self._config_manager.get_provider_config()
            if provider_config:
                for provider_instance in provider_config.get_active_providers():
                    if not self._provider_registry.is_provider_instance_registered(
                        provider_instance.name
                    ):
                        self._provider_registry.ensure_provider_instance_registered_from_config(
                            provider_instance
                        )
        except Exception as e:
            self.logger.error("Failed to register configured providers: %s", e, exc_info=True)

    def _log_provider_configuration(self, config_manager) -> None:
        """Log provider configuration information during initialization."""
        try:
            # Check if consolidated provider configuration is available
            if hasattr(config_manager, "get_provider_config"):
                provider_config = config_manager.get_provider_config()
                if provider_config and hasattr(provider_config, "get_mode"):
                    mode = provider_config.get_mode()
                    active_providers = provider_config.get_active_providers()

                    self.logger.info("Provider configuration mode: %s", mode.value)
                    self.logger.info("Active providers: %s", [p.name for p in active_providers])

                    if mode.value == "multi":
                        self.logger.info("Selection policy: %s", provider_config.selection_policy)
                        self.logger.info(
                            "Health check interval: %ss",
                            provider_config.health_check_interval,
                        )
                else:
                    self.logger.info("Provider configuration not found")

            elif hasattr(config_manager, "is_provider_strategy_enabled"):
                if config_manager.is_provider_strategy_enabled():
                    self.logger.info("Provider strategy enabled but configuration not available")
                else:
                    self.logger.info("Provider strategy configuration not enabled")
            else:
                self.logger.info("Provider strategy configuration not available")

        except Exception as e:
            self.logger.debug("Could not log provider configuration details: %s", str(e))

    async def _preload_templates(self) -> None:
        """Pre-load templates into cache during initialization."""
        try:
            # Template preloading will be handled by the template service
            # when first accessed through CQRS queries
            self.logger.debug("Template cache will be warmed up on first access")
        except Exception as e:
            self.logger.debug("Could not pre-load templates: %s", str(e))

    def _log_final_provider_info(self) -> None:
        """Log final provider information after initialization."""
        try:
            if hasattr(self, "_provider_registry") and self._provider_registry:
                available_providers = self._provider_registry.get_registered_providers()
                available_instances = self._provider_registry.get_registered_provider_instances()

                self.logger.info("Provider types available: %s", available_providers)
                self.logger.info("Provider instances available: %s", available_instances)
            elif hasattr(self, "provider_type"):
                self.logger.info("Provider type: %s", self.provider_type)

        except Exception as e:
            self.logger.debug("Could not log final provider info: %s", str(e))

    def get_query_bus(self):
        """Get the query bus for CQRS operations (cached after first access)."""
        if not self._initialized:
            raise RuntimeError("Application not initialized")

        # Cache the query bus after first lookup for performance
        if not hasattr(self, "_query_bus"):
            from orb.infrastructure.di.buses import QueryBus

            self._query_bus = self._container.get(QueryBus)
        return self._query_bus

    def get_command_bus(self):
        """Get the command bus for CQRS operations (cached after first access)."""
        if not self._initialized:
            raise RuntimeError("Application not initialized")

        # Cache the command bus after first lookup for performance
        if not hasattr(self, "_command_bus"):
            from orb.infrastructure.di.buses import CommandBus

            self._command_bus = self._container.get(CommandBus)
        return self._command_bus

    def get_provider_info(self) -> dict[str, Any]:
        """Get provider information using provider registry."""
        if not self._initialized:
            return {"status": "not_initialized"}

        try:
            if hasattr(self, "_provider_registry") and self._provider_registry:
                available_providers = self._provider_registry.get_registered_providers()
                available_instances = self._provider_registry.get_registered_provider_instances()

                return {
                    "status": "configured",
                    "mode": "multi" if len(available_instances) > 1 else "single",
                    "provider_types": available_providers,
                    "provider_instances": available_instances,
                    "provider_names": available_instances,
                    "provider_count": len(available_instances),
                    "initialized": True,
                }
            else:
                return {
                    "status": "not_configured",
                    "provider_type": self.provider_type,
                    "initialized": False,
                }
        except Exception as e:
            self.logger.error("Failed to get provider info: %s", e, exc_info=True)
            return {"status": "error", "error": str(e), "initialized": False}

    def health_check(self) -> dict[str, Any]:
        """Check application health using provider registry."""
        if not self._initialized:
            return {"status": "error", "message": "Application not initialized"}

        try:
            if hasattr(self, "_provider_registry") and self._provider_registry:
                # Check provider health
                available_instances = self._provider_registry.get_registered_provider_instances()
                healthy_providers = 0
                provider_health = {}

                for instance_name in available_instances:
                    try:
                        health_status = self._provider_registry.get_or_create_strategy(
                            instance_name
                        )
                        is_healthy = (
                            health_status.check_health().is_healthy
                            if health_status and hasattr(health_status, "check_health")
                            else False
                        )
                        provider_health[instance_name] = is_healthy
                        if is_healthy:
                            healthy_providers += 1
                    except Exception as e:
                        self.logger.warning(
                            "Health check failed for %s: %s", instance_name, e, exc_info=True
                        )
                        provider_health[instance_name] = False

                total_providers = len(available_instances)

                # Determine overall status
                if total_providers == 0:
                    status = "warning"
                    message = "No providers configured"
                elif healthy_providers == total_providers:
                    status = "healthy"
                    message = f"All {total_providers} provider(s) healthy"
                elif healthy_providers > 0:
                    status = "degraded"
                    message = f"{healthy_providers}/{total_providers} provider(s) healthy"
                else:
                    status = "unhealthy"
                    message = f"All {total_providers} provider(s) unhealthy"

                return {
                    "status": status,
                    "message": message,
                    "providers": provider_health,
                    "initialized": self._initialized,
                    "provider_count": total_providers,
                    "healthy_provider_count": healthy_providers,
                }
            else:
                return {
                    "status": "warning",
                    "message": "Provider registry not available",
                    "initialized": self._initialized,
                }

        except Exception as e:
            self.logger.error("Health check failed: %s", e, exc_info=True)
            return {
                "status": "error",
                "message": f"Health check failed: {e!s}",
                "initialized": self._initialized,
            }

    def shutdown(self) -> None:
        """Shutdown the application."""
        self.logger.info("Shutting down application")
        self._initialized = False

    async def cleanup(self) -> None:
        """Async cleanup — delegates to synchronous shutdown."""
        self.shutdown()

    async def __aenter__(self) -> Application:
        """Async context manager entry."""
        if not await self.initialize():
            raise RuntimeError("Failed to initialize application")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        self.shutdown()


async def create_application(config_path: Optional[str] = None) -> Application:
    """Create and initialize a provider-aware application."""
    app = Application(config_path)
    if not await app.initialize():
        raise RuntimeError(f"Failed to initialize application with {app.provider_type} provider")
    return app


async def main() -> None:
    """Serve as main entry point for provider-aware application."""
    import os
    import sys

    # Get provider type from environment or config
    config_path = os.getenv("ORB_CONFIG_FILE")

    # Only print before app creation - no logger available yet
    print("Starting Open Host Factory...")

    try:
        async with await create_application(config_path) as app:
            # Use existing app.logger - no need to create new logger
            app.logger.info(
                "Application started successfully with %s provider",
                (app.provider_type or "unknown").upper(),
            )

            # Get provider info
            provider_info = app.get_provider_info()
            if "provider_names" in provider_info:
                app.logger.info("Provider names: %s", provider_info["provider_names"])
            elif hasattr(app, "provider_type"):
                app.logger.info("Provider type: %s", app.provider_type)
            app.logger.info("Status: %s", provider_info.get("initialized", False))

            # Health check
            health = app.health_check()
            app.logger.info("Health check status: %s", health.get("status"))

            # Application is ready - in production this would start the API server
            app.logger.info("Application initialized and ready")
            app.logger.info("In production, this would start the API server")

    except Exception as e:
        # Keep print here - app creation failed, no logger available
        print(f"Application failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

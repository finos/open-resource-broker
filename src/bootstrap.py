"""Application bootstrap - DI-based architecture."""

from __future__ import annotations

from typing import Any, Dict, Optional

# Import configuration
from src.config import AppConfig

# Import logging
from src.infrastructure.logging.logger import get_logger, setup_logging

# Import DI container


class Application:
    """DI-based application context manager with registration pattern."""

    def __init__(self, config_path: Optional[str] = None) -> None:
        """Initialize the instance."""
        self.config_path = config_path
        self._initialized = False

        # Defer heavy initialization until first use
        self._container = None
        self._config_manager = None
        self._domain_container_set = False
        self.provider_type = None
        self._dry_run_context = None

        # Only create logger immediately (lightweight)
        self.logger = get_logger(__name__)

    def _ensure_container(self):
        """Ensure DI container is created (lazy initialization)."""
        if self._container is None:
            from src.infrastructure.di.container import get_container

            self._container = get_container()

            # Set up domain container for decorators
            if not self._domain_container_set:
                from src.domain.base.decorators import set_domain_container

                set_domain_container(self._container)
                self._domain_container_set = True

    def _ensure_config_manager(self):
        """Ensure config manager is created (lazy initialization)."""
        if self._config_manager is None:
            from src.config.manager import get_config_manager

            self._config_manager = get_config_manager(self.config_path)

            # Extract provider type from config
            provider_config = self._config_manager.get("provider", {"type": "mock"})
            if isinstance(provider_config, dict):
                self.provider_type = provider_config.get("type", "mock")
            else:
                self.provider_type = str(provider_config)

    async def initialize(self, dry_run: bool = False) -> bool:
        """Initialize the application with DI container."""
        try:
            # Ensure config manager is available (lazy)
            self._ensure_config_manager()

            self.logger.info(f"Initializing application with provider: {self.provider_type}")

            # Log provider configuration information
            self._log_provider_configuration(self._config_manager)

            # Setup logging
            app_config = self._config_manager.get_typed(AppConfig)
            setup_logging(app_config.logging)

            # Activate dry-run context if requested
            if dry_run:
                from src.infrastructure.mocking.dry_run_context import dry_run_context

                self.logger.info("DRY-RUN mode activated during application initialization")
                self._dry_run_context = dry_run_context(True)
                self._dry_run_context.__enter__()

            # Ensure container is available (lazy)
            self._ensure_container()

            # Register all services AFTER container creation but BEFORE service
            # resolution
            from src.infrastructure.di.services import register_all_services

            register_all_services(self._container)

            # Initialize provider context directly
            from src.providers.base.strategy import ProviderContext

            self._provider_context = self._container.get(ProviderContext)

            # Initialize provider context based on loading mode
            if not self._container.is_lazy_loading_enabled():
                # Eager loading - initialize immediately
                if (
                    hasattr(self._provider_context, "initialize")
                    and not self._provider_context.initialize()
                ):
                    self.logger.warning("Provider context initialization returned False")
            else:
                # Lazy loading - just mark as ready, don't trigger loading
                self.logger.info("Lazy loading enabled - providers will initialize on first use")
                if hasattr(self._provider_context, "_initialized"):
                    self._provider_context._initialized = True

            # Pre-load templates into cache properly during initialization
            await self._preload_templates()

            # Log final provider information
            self._log_final_provider_info()

            self._initialized = True
            self.logger.info(
                f"Open HostFactory Plugin initialized successfully with {self.provider_type} provider"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize application: {e}")
            return False

    def _log_provider_configuration(self, config_manager) -> None:
        """Log provider configuration information during initialization."""
        try:
            # Check if consolidated provider configuration is available
            if hasattr(config_manager, "get_provider_config"):
                provider_config = config_manager.get_provider_config()
                if provider_config:
                    mode = provider_config.get_mode()
                    active_providers = provider_config.get_active_providers()

                    self.logger.info(f"Provider configuration mode: {mode.value}")
                    self.logger.info(f"Active providers: {[p.name for p in active_providers]}")

                    if mode.value == "multi":
                        self.logger.info(f"Selection policy: {provider_config.selection_policy}")
                        self.logger.info(
                            f"Health check interval: {provider_config.health_check_interval}s"
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
            self.logger.debug(f"Could not log provider configuration details: {str(e)}")

    async def _preload_templates(self) -> None:
        """Pre-load templates into cache during initialization."""
        try:
            # Template preloading will be handled by the template service
            # when first accessed through CQRS queries
            self.logger.debug("Template cache will be warmed up on first access")
        except Exception as e:
            self.logger.debug(f"Could not pre-load templates: {str(e)}")

    def _log_final_provider_info(self) -> None:
        """Log final provider information after initialization."""
        try:
            if hasattr(self, "_provider_context") and self._provider_context:
                available_strategies = self._provider_context.available_strategies
                current_strategy = self._provider_context.current_strategy_type

                self.logger.info(f"Provider strategies available: {available_strategies}")
                self.logger.info(f"Current provider strategy: {current_strategy}")
            elif hasattr(self, "provider_type"):
                self.logger.info(f"Provider type: {self.provider_type}")

        except Exception as e:
            self.logger.debug(f"Could not log final provider info: {str(e)}")

    def get_query_bus(self):
        """Get the query bus for CQRS operations (cached after first access)."""
        if not self._initialized:
            raise RuntimeError("Application not initialized")

        # Cache the query bus after first lookup for performance
        if not hasattr(self, "_query_bus"):
            from src.infrastructure.di.buses import QueryBus

            self._query_bus = self._container.get(QueryBus)
        return self._query_bus

    def get_command_bus(self):
        """Get the command bus for CQRS operations (cached after first access)."""
        if not self._initialized:
            raise RuntimeError("Application not initialized")

        # Cache the command bus after first lookup for performance
        if not hasattr(self, "_command_bus"):
            from src.infrastructure.di.buses import CommandBus

            self._command_bus = self._container.get(CommandBus)
        return self._command_bus

    def get_provider_info(self) -> Dict[str, Any]:
        """Get provider information using direct provider context."""
        if not self._initialized:
            return {"status": "not_initialized"}

        try:
            if hasattr(self, "_provider_context") and self._provider_context:
                available_strategies = self._provider_context.available_strategies
                current_strategy = self._provider_context.current_strategy_type

                return {
                    "status": "configured",
                    "mode": "multi" if len(available_strategies) > 1 else "single",
                    "current_strategy": current_strategy,
                    "available_strategies": available_strategies,
                    "provider_names": available_strategies,
                    "provider_count": len(available_strategies),
                    "initialized": True,
                }
            else:
                return {
                    "status": "not_configured",
                    "provider_type": self.provider_type,
                    "initialized": False,
                }
        except Exception as e:
            self.logger.error(f"Failed to get provider info: {e}")
            return {"status": "error", "error": str(e), "initialized": False}

    def health_check(self) -> Dict[str, Any]:
        """Check application health using direct provider context."""
        if not self._initialized:
            return {"status": "error", "message": "Application not initialized"}

        try:
            if hasattr(self, "_provider_context") and self._provider_context:
                # Check provider health
                available_strategies = self._provider_context.available_strategies
                healthy_providers = 0
                provider_health = {}

                for strategy_name in available_strategies:
                    try:
                        health_status = self._provider_context.check_strategy_health(strategy_name)
                        is_healthy = (
                            health_status and health_status.is_healthy if health_status else False
                        )
                        provider_health[strategy_name] = is_healthy
                        if is_healthy:
                            healthy_providers += 1
                    except Exception as e:
                        self.logger.warning(f"Health check failed for {strategy_name}: {e}")
                        provider_health[strategy_name] = False

                total_providers = len(available_strategies)

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
                    "message": "Provider context not available",
                    "initialized": self._initialized,
                }

        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return {
                "status": "error",
                "message": f"Health check failed: {str(e)}",
                "initialized": self._initialized,
            }

    def shutdown(self) -> None:
        """Shutdown the application."""
        self.logger.info("Shutting down application")
        self._initialized = False

    async def __aenter__(self) -> "Application":
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
    config_path = os.getenv("CONFIG_PATH")

    # Only print before app creation - no logger available yet
    print("Starting Open Host Factory...")  # noqa: bootstrap output

    try:
        async with await create_application(config_path) as app:
            # Use existing app.logger - no need to create new logger
            app.logger.info(
                f"Application started successfully with {app.provider_type.upper()} provider"
            )

            # Get provider info
            provider_info = app.get_provider_info()
            if "provider_names" in provider_info:
                app.logger.info(f"Provider names: {provider_info['provider_names']}")
            elif hasattr(app, "provider_type"):
                app.logger.info(f"Provider type: {app.provider_type}")
            app.logger.info(f"Status: {provider_info.get('initialized', False)}")

            # Health check
            health = app.health_check()
            app.logger.info(f"Health check status: {health.get('status')}")

            # Application is ready - in production this would start the API server
            app.logger.info("Application initialized and ready")
            app.logger.info("In production, this would start the API server")

    except Exception as e:
        # Keep print here - app creation failed, no logger available
        print(f"Application failed: {e}")  # noqa: bootstrap error
        sys.exit(1)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

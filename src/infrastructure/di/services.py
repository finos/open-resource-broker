"""Service registration orchestrator for dependency injection.

This module coordinates the registration of all services across different layers:
- Core services (logging, configuration, metrics)
- Provider services (AWS, strategy patterns)
- Infrastructure services (repositories, templates)
- CQRS handlers (commands and queries)
- Server services (FastAPI, REST API handlers)
"""

from typing import Any, Dict, Optional

from infrastructure.di.container import DIContainer, get_container

# Import focused service registration modules
from infrastructure.di.core_services import register_core_services
from infrastructure.di.infrastructure_services import register_infrastructure_services
from infrastructure.di.provider_services import register_provider_services
from infrastructure.di.scheduler_services import register_scheduler_services
from infrastructure.di.server_services import register_server_services
from infrastructure.di.storage_services import register_storage_services


def register_all_services(container: Optional[DIContainer] = None) -> DIContainer:
    """
    Register all services in the dependency injection container.
    Includes lazy loading support for improved startup performance.

    Args:
        container: Optional container instance

    Returns:
        Configured container
    """
    if container is None:
        container = get_container()

    # Check if lazy loading is enabled
    if container.is_lazy_loading_enabled():
        return _register_services_lazy(container)
    else:
        return _register_services_eager(container)


def _register_services_lazy(container: DIContainer) -> DIContainer:
    """Register services using lazy loading approach."""
    from infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    logger.info("Registering services with lazy loading enabled")

    # 1. Register only essential services immediately
    from infrastructure.di.port_registrations import register_port_adapters

    register_port_adapters(container)

    register_core_services(container)

    # 2. Register configured storage strategy only
    register_storage_services(container)

    # 3. Register configured scheduler strategy only
    register_scheduler_services(container)

    # 4. Register provider services immediately (fix for provider
    # context errors)
    register_provider_services(container)

    # 5. Register infrastructure services immediately (needed for template system)
    register_infrastructure_services(container)

    # 6. Register lazy factories for non-essential services
    _register_lazy_service_factories(container)

    logger.info("Lazy service registration complete")
    return container


def _register_services_eager(container: DIContainer) -> DIContainer:
    """Register services using traditional eager loading approach."""
    from infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    logger.info("Registering services with eager loading (fallback mode)")

    # Register services in dependency order (original behavior)
    # 1. Register scheduler strategies first (needed by port adapters)
    from infrastructure.scheduler.registration import register_all_scheduler_types

    register_all_scheduler_types()

    # 2. Register port adapters FIRST (provides LoggingPort, ConfigurationPort, etc.)
    from infrastructure.di.port_registrations import register_port_adapters

    register_port_adapters(container)

    # 3. Register core services (uses LoggingPort from port adapters)
    register_core_services(container)

    # 4. Setup CQRS infrastructure (handlers and buses)
    from infrastructure.di.container import _setup_cqrs_infrastructure

    _setup_cqrs_infrastructure(container)

    # 5. Register provider services (needed by infrastructure services)
    register_provider_services(container)

    # 6. Register infrastructure services
    register_infrastructure_services(container)

    # CQRS handlers are automatically discovered and registered by _setup_cqrs_infrastructure
    # No manual registration needed - Handler Discovery System handles everything

    # 7. Register server services (conditionally based on config)
    register_server_services(container)

    return container


def _register_lazy_service_factories(container: DIContainer) -> None:
    """Register lazy factories for services that can be loaded on-demand."""
    from infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    # Register CQRS infrastructure as lazy - triggered by QueryBus or CommandBus access
    def setup_cqrs_lazy(c):
        """Setup CQRS infrastructure lazily when needed."""
        from infrastructure.di.container import _setup_cqrs_infrastructure

        _setup_cqrs_infrastructure(c)

    from infrastructure.di.buses import CommandBus, QueryBus

    container.register_on_demand(QueryBus, setup_cqrs_lazy)
    container.register_on_demand(CommandBus, setup_cqrs_lazy)

    # Provider services are now registered immediately in lazy mode
    # No need for lazy provider registration

    # Register infrastructure services as lazy - triggered by first
    # infrastructure service access
    def register_infrastructure_lazy(c):
        """Register infrastructure services lazily when first accessed."""
        register_infrastructure_services(c)
        # Also setup CQRS if not already done (infrastructure services may need buses)
        if not c.has(QueryBus):
            setup_cqrs_lazy(c)

    # Register infrastructure services on-demand when needed
    # Use a placeholder type for infrastructure services
    container.register_on_demand(
        type("InfrastructureServices", (), {}), register_infrastructure_lazy
    )

    # Register scheduler services as lazy
    def register_scheduler_lazy(c):
        """Register scheduler services lazily when needed."""
        from infrastructure.scheduler.registration import register_active_scheduler_only

        # Get scheduler type from config if available
        try:
            from config.manager import get_config_manager

            config_manager = get_config_manager()
            scheduler_config = config_manager.get("scheduler", {"type": "default"})
            scheduler_type = (
                scheduler_config.get("type", "default")
                if isinstance(scheduler_config, dict)
                else str(scheduler_config)
            )
            register_active_scheduler_only(scheduler_type)
        except Exception:
            # Fallback to default scheduler
            register_active_scheduler_only("default")

    # Register scheduler on-demand when SchedulerPort is accessed
    from domain.base.ports.scheduler_port import SchedulerPort

    container.register_on_demand(SchedulerPort, register_scheduler_lazy)

    # Register server services as lazy
    def register_server_lazy(c):
        """Register server services lazily when needed."""
        register_server_services(c)

    # Use a placeholder type for server services
    container.register_on_demand(type("ServerServices", (), {}), register_server_lazy)

    logger.debug("Lazy service factories registered")


def create_handler(handler_class, config: Optional[Dict[str, Any]] = None) -> Any:
    """
    Create an API handler with dependencies.

    Args:
        handler_class: Handler class to create
        config: Optional configuration

    Returns:
        Created handler instance
    """
    # Ensure services are registered
    container = register_all_services()

    # Register handler class if not already registered
    if handler_class not in container._factories:
        # Get logger
        from infrastructure.logging.logger import get_logger

        logger = get_logger(__name__)

        # Register handler class directly if it uses @injectable
        try:
            from infrastructure.di.decorators import is_injectable

            if is_injectable(handler_class):
                logger.info("Registering injectable handler class %s", handler_class.__name__)
                container.register_singleton(handler_class)
            else:
                # Legacy handler registration
                logger.info("Registering legacy handler class %s", handler_class.__name__)

                def handler_factory(c):
                    """Factory for legacy handler instances."""
                    # Get CQRS buses directly from container
                    from infrastructure.di.buses import CommandBus, QueryBus
                    from monitoring.metrics import MetricsCollector

                    query_bus = c.get(QueryBus)
                    command_bus = c.get(CommandBus)

                    # Get metrics collector from container if available
                    metrics = c.get_optional(MetricsCollector)

                    # Create handler with CQRS dependencies
                    return handler_class(query_bus, command_bus, metrics)

                container.register_factory(handler_class, handler_factory)
        except ImportError:
            # Fallback to CQRS registration if decorator module not available
            logger.info("Fallback CQRS registration for handler class %s", handler_class.__name__)

            def handler_factory(c):
                """Fallback factory for CQRS handler instances."""
                # Get CQRS buses directly from container
                from infrastructure.di.buses import CommandBus, QueryBus
                from monitoring.metrics import MetricsCollector

                query_bus = c.get(QueryBus)
                command_bus = c.get(CommandBus)

                # Get metrics collector from container if available
                metrics = c.get_optional(MetricsCollector)

                # Create handler with CQRS dependencies
                return handler_class(query_bus, command_bus, metrics)

            container.register_factory(handler_class, handler_factory)

    # Get handler from container
    return container.get(handler_class)

"""Service registration orchestrator for dependency injection.

This module coordinates the registration of all services across different layers:
- Core services (logging, configuration, metrics)
- Provider services (AWS, strategy patterns)
- Infrastructure services (repositories, templates)
- CQRS handlers (commands and queries)
- Server services (FastAPI, REST API handlers)
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orb.infrastructure.di.container import DIContainer

# Import focused service registration modules
from orb.infrastructure.di.core_services import register_core_services
from orb.infrastructure.di.domain_services import register_domain_services
from orb.infrastructure.di.infrastructure_services import register_infrastructure_services
from orb.infrastructure.di.monitoring_services import register_monitoring_services
from orb.infrastructure.di.provider_services import register_provider_services
from orb.infrastructure.di.server_services import register_server_services
from orb.infrastructure.di.storage_services import register_storage_services


def register_all_services(container: "DIContainer") -> "DIContainer":
    """Register all services in the dependency injection container.

    Args:
        container: Container instance (required)

    Returns:
        Configured container
    """
    if container.is_lazy_loading_enabled():
        return _register_services_lazy(container)
    else:
        return _register_services_eager(container)


def setup_cqrs_infrastructure(container: "DIContainer") -> None:
    """Set up CQRS infrastructure: handler discovery and buses."""
    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    try:
        from orb.domain.base.ports.logging_port import LoggingPort
        from orb.infrastructure.di.buses import BusFactory, CommandBus, QueryBus
        from orb.infrastructure.di.handler_discovery import create_handler_discovery_service

        logger.info("Setting up CQRS infrastructure")

        if container.is_lazy_loading_enabled():
            logger.info("Ensuring infrastructure services are available for CQRS setup")
            _ensure_infrastructure_services(container)

        discovery_service = create_handler_discovery_service(container)
        discovery_service.discover_and_register_handlers()

        try:
            from orb.application.decorators import get_handler_registry_stats

            stats = get_handler_registry_stats()
            logger.info("Handler discovery results: %s", stats)
        except ImportError:
            logger.debug("Handler registry stats not available")

        logging_port = container.get(LoggingPort)
        query_bus, command_bus = BusFactory.create_buses(container, logging_port)
        container.register_instance(QueryBus, query_bus)
        container.register_instance(CommandBus, command_bus)
        from orb.application.ports.command_bus_port import CommandBusPort
        from orb.application.ports.query_bus_port import QueryBusPort

        container.register_instance(QueryBusPort, query_bus)
        container.register_instance(CommandBusPort, command_bus)

        logger.info("CQRS infrastructure setup complete")

    except ImportError as e:
        logger.debug("CQRS infrastructure not available: %s", e)
    except Exception as e:
        logger.warning("Failed to setup CQRS infrastructure: %s", e, exc_info=True)


def _ensure_infrastructure_services(container: "DIContainer") -> None:
    """Ensure infrastructure services are registered before CQRS setup."""
    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    try:
        register_infrastructure_services(container)
        logger.debug("Infrastructure services ensured for CQRS setup")
    except Exception as e:
        logger.warning("Failed to ensure infrastructure services: %s", e, exc_info=True)


def _register_services_lazy(container: "DIContainer") -> "DIContainer":
    """Register services using lazy loading approach."""
    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)
    logger.info("Registering services with lazy loading enabled")

    # 0. Register all types first (required for any registry operations)
    from orb.infrastructure.scheduler.registration import register_all_scheduler_types
    from orb.infrastructure.storage.registration import register_all_storage_types
    from orb.providers.registration import register_all_provider_types

    register_all_scheduler_types()
    register_all_storage_types()
    register_all_provider_types()

    # 1. Register ConfigurationManager FIRST (port adapters depend on it)
    from orb.config.managers.configuration_manager import ConfigurationManager

    container.register_singleton(ConfigurationManager, lambda c: ConfigurationManager())

    # 2. Register port adapters (now ConfigurationManager is available)
    from orb.infrastructure.di.port_registrations import register_port_adapters

    register_port_adapters(container)

    # 3. Register remaining core services
    register_core_services(container)

    # 4. Register domain services
    register_domain_services(container)

    # 5. Register configured storage strategy only
    register_storage_services(container)

    # 6. Register registry services
    from orb.infrastructure.di.registry_services import register_registry_services

    register_registry_services(container)

    # 8. Register provider services immediately (fix for provider context errors)
    register_provider_services(container)

    # 9. Register infrastructure services immediately (needed for template system)
    register_infrastructure_services(container)

    # 9a. Register monitoring services (cross-cutting concern, not provider-specific)
    register_monitoring_services(container)

    # 10. Setup CQRS infrastructure (handlers must be registered before buses are used)
    setup_cqrs_infrastructure(container)

    # 11. Register lazy factories for non-essential services
    _register_lazy_service_factories(container)

    logger.info("Lazy service registration complete")
    return container


def _register_services_eager(container: "DIContainer") -> "DIContainer":
    """Register services using traditional eager loading approach."""
    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)
    logger.info("Registering services with eager loading (fallback mode)")

    from orb.infrastructure.scheduler.registration import register_all_scheduler_types
    from orb.infrastructure.storage.registration import register_all_storage_types
    from orb.providers.registration import register_all_provider_types

    register_all_scheduler_types()
    register_all_storage_types()
    register_all_provider_types()

    from orb.infrastructure.di.port_registrations import register_port_adapters

    register_port_adapters(container)
    register_core_services(container)
    register_domain_services(container)
    setup_cqrs_infrastructure(container)
    register_provider_services(container)
    register_infrastructure_services(container)
    register_monitoring_services(container)
    register_server_services(container)

    return container


def _register_lazy_service_factories(container: "DIContainer") -> None:
    """Register lazy factories for services that can be loaded on-demand.

    Note: CQRS infrastructure and provider services are now registered
    immediately in lazy mode, so no additional lazy registration is needed.
    """
    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    logger.debug("Lazy service factories registered")

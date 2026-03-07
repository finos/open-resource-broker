"""Registry services DI registration."""

from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.di.container import DIContainer


def register_registry_services(container: DIContainer) -> None:
    """Register all registry application services."""
    from orb.application.services.scheduler_registry_service import SchedulerRegistryService
    from orb.application.services.storage_registry_service import StorageRegistryService
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.infrastructure.scheduler.registry import get_scheduler_registry
    from orb.infrastructure.storage.registry import get_storage_registry
    from orb.providers.registry import get_provider_registry

    # Inject config_port into provider registry now that ConfigurationPort is available
    get_provider_registry()._config_port = container.get(ConfigurationPort)

    # Scheduler registry service
    container.register_singleton(
        SchedulerRegistryService,
        lambda c: SchedulerRegistryService(
            registry=get_scheduler_registry(),  # type: ignore[arg-type]
            logger=c.get(LoggingPort),
        ),
    )

    # Storage registry service
    container.register_singleton(
        StorageRegistryService,
        lambda c: StorageRegistryService(
            registry=get_storage_registry(),  # type: ignore[arg-type]
            logger=c.get(LoggingPort),
        ),
    )

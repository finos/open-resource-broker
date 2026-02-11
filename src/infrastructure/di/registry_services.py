"""Registry services DI registration."""

from infrastructure.di.container import DIContainer
from domain.base.ports.logging_port import LoggingPort


def register_registry_services(container: DIContainer) -> None:
    """Register all registry application services."""
    from application.services.scheduler_registry_service import SchedulerRegistryService
    from application.services.storage_registry_service import StorageRegistryService
    from infrastructure.scheduler.registry import get_scheduler_registry
    from infrastructure.storage.registry import get_storage_registry
    
    # Scheduler registry service
    container.register_singleton(
        SchedulerRegistryService,
        lambda c: SchedulerRegistryService(
            registry=get_scheduler_registry(),
            logger=c.get(LoggingPort)
        )
    )
    
    # Storage registry service
    container.register_singleton(
        StorageRegistryService,
        lambda c: StorageRegistryService(
            registry=get_storage_registry(),
            logger=c.get(LoggingPort)
        )
    )
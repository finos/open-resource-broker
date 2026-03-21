"""Registry services DI registration."""

from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.di.container import DIContainer


def register_registry_services(container: DIContainer) -> None:
    """Register all registry application services."""
    from orb.application.services.scheduler_registry_service import SchedulerRegistryService
    from orb.application.services.storage_registry_service import StorageRegistryService
    from orb.infrastructure.scheduler.registry import get_scheduler_registry
    from orb.infrastructure.storage.registry import get_storage_registry

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

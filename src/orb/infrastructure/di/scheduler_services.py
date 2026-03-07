"""Scheduler service registrations for dependency injection."""

from orb.domain.base.ports import ConfigurationPort
from orb.infrastructure.di.container import DIContainer
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.scheduler.factory import SchedulerStrategyFactory


def register_scheduler_services(container: DIContainer) -> None:
    """Register scheduler services with configuration-driven strategy loading."""

    # Register scheduler strategy factory with lazy initialization
    def create_scheduler_factory(c):
        config = c.get(ConfigurationPort)
        return SchedulerStrategyFactory(config_manager=config)

    container.register_factory(SchedulerStrategyFactory, create_scheduler_factory)

    # Register only the configured scheduler strategy
    _register_configured_scheduler_strategy(container)


def _register_configured_scheduler_strategy(container: DIContainer) -> None:
    """Register only the configured scheduler strategy."""
    from orb.infrastructure.scheduler.registry import get_scheduler_registry

    registry = get_scheduler_registry()
    registry.ensure_type_registered("default")

    logger = get_logger(__name__)
    logger.info("Registered fallback scheduler strategy: default")

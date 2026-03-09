"""Scheduler service registrations for dependency injection."""

from orb.domain.base.ports import ConfigurationPort
from orb.infrastructure.di.container import DIContainer
from orb.infrastructure.scheduler.factory import SchedulerStrategyFactory


def register_scheduler_services(container: DIContainer) -> None:
    """Register scheduler services with configuration-driven strategy loading."""

    def create_scheduler_factory(c):
        config = c.get(ConfigurationPort)
        return SchedulerStrategyFactory(config_manager=config)

    container.register_factory(SchedulerStrategyFactory, create_scheduler_factory)

"""Core service registrations for dependency injection."""

from src.domain.base.ports import (
    ConfigurationPort,
    EventPublisherPort,
    LoggingPort,
    SchedulerPort,
)
from src.infrastructure.di.buses import CommandBus, QueryBus
from src.infrastructure.di.container import DIContainer
from src.monitoring.metrics import MetricsCollector


def register_core_services(container: DIContainer) -> None:
    """Register core application services."""

    # Register metrics collector
    container.register_singleton(MetricsCollector)

    # Register template format converter

    # Register scheduler strategy
    container.register_factory(SchedulerPort, lambda c: _create_scheduler_strategy(c))

    # Register event publisher
    from src.infrastructure.events.publisher import ConfigurableEventPublisher

    container.register_factory(
        EventPublisherPort,
        lambda c: ConfigurableEventPublisher(mode="logging"),  # Default to logging mode
    )

    # Register command and query buses with factory functions
    container.register_factory(
        CommandBus, lambda c: CommandBus(container=c, logger=c.get(LoggingPort))
    )

    container.register_factory(QueryBus, lambda c: QueryBus(container=c, logger=c.get(LoggingPort)))


def _create_scheduler_strategy(container: DIContainer) -> SchedulerPort:
    """Create scheduler strategy using factory."""
    from src.infrastructure.factories.scheduler_strategy_factory import (
        SchedulerStrategyFactory,
    )

    factory = container.get(SchedulerStrategyFactory)
    config = container.get(ConfigurationPort)
    scheduler_type = config.get_scheduler_strategy()
    return factory.create_strategy(scheduler_type, container)

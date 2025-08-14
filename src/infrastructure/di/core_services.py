"""Core service registrations for dependency injection."""

from src.config.manager import ConfigurationManager
from src.domain.base.ports import EventPublisherPort, LoggingPort, SchedulerPort
from src.infrastructure.di.buses import CommandBus, QueryBus
from src.infrastructure.di.container import DIContainer
from src.infrastructure.registry.scheduler_registry import get_scheduler_registry
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
    """Create scheduler strategy from registry."""
    registry = get_scheduler_registry()
    config_manager = container.get(ConfigurationManager)
    scheduler_type = config_manager.get_scheduler_strategy()
    return registry.create_strategy(scheduler_type, container)

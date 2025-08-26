"""Core service registrations for dependency injection."""

from domain.base.ports import (
    ConfigurationPort,
    EventPublisherPort,
    LoggingPort,
    ProviderPort,
    SchedulerPort,
    StoragePort,
)
from infrastructure.di.buses import CommandBus, QueryBus
from infrastructure.di.container import DIContainer
from monitoring.metrics import MetricsCollector


def register_core_services(container: DIContainer) -> None:
    """Register core application services."""

    # Register metrics collector
    container.register_singleton(MetricsCollector)

    # Register template format converter

    # Register scheduler strategy
    container.register_factory(SchedulerPort, lambda c: _create_scheduler_strategy(c))

    # Register storage strategy
    container.register_factory(StoragePort, lambda c: _create_storage_strategy(c))

    # Register provider strategy
    container.register_factory(ProviderPort, lambda c: _create_provider_strategy(c))

    # Register event publisher
    from infrastructure.events.publisher import ConfigurableEventPublisher

    container.register_factory(
        EventPublisherPort,
        lambda c: ConfigurableEventPublisher(mode="logging"),  # Default to logging mode
    )

    # Register command and query buses with factory functions
    container.register_factory(
        CommandBus, lambda c: CommandBus(container=c, logger=c.get(LoggingPort))
    )

    container.register_factory(QueryBus, lambda c: QueryBus(container=c, logger=c.get(LoggingPort)))

    # Register native spec service
    def create_native_spec_service(c):
        """Create native spec service."""
        from application.services.native_spec_service import NativeSpecService
        from domain.base.ports.spec_rendering_port import SpecRenderingPort

        return NativeSpecService(
            config_port=c.get(ConfigurationPort), spec_renderer=c.get(SpecRenderingPort)
        )

    from application.services.native_spec_service import NativeSpecService

    container.register_factory(NativeSpecService, create_native_spec_service)


def _create_scheduler_strategy(container: DIContainer) -> SchedulerPort:
    """Create scheduler strategy using factory."""
    from infrastructure.factories.scheduler_strategy_factory import (
        SchedulerStrategyFactory,
    )

    factory = container.get(SchedulerStrategyFactory)
    config = container.get(ConfigurationPort)
    scheduler_type = config.get_scheduler_strategy()
    return factory.create_strategy(scheduler_type, container)


def _create_storage_strategy(container: DIContainer) -> StoragePort:
    """Create storage strategy using factory."""
    from infrastructure.factories.storage_strategy_factory import StorageStrategyFactory

    factory = container.get(StorageStrategyFactory)
    config = container.get(ConfigurationPort)
    storage_type = config.get_storage_strategy()
    return factory.create_strategy(storage_type, config)


def _create_provider_strategy(container: DIContainer) -> ProviderPort:
    """Create provider strategy using adapter pattern."""
    from infrastructure.adapters.provider_context_adapter import ProviderContextAdapter
    from providers.base.strategy.provider_context import ProviderContext

    provider_context = container.get(ProviderContext)
    return ProviderContextAdapter(provider_context)

"""Core service registrations for dependency injection."""

from orb.application.ports.scheduler_port import SchedulerPort
from orb.config.managers.configuration_manager import ConfigurationManager
from orb.domain.base.ports import (
    ConfigurationPort,
    EventPublisherPort,
    LoggingPort,
    ProviderDiscoveryPort,
    StoragePort,
)
from orb.infrastructure.di.buses import CommandBus, QueryBus
from orb.infrastructure.di.container import DIContainer
from orb.monitoring.metrics import MetricsCollector


def register_core_services(container: DIContainer) -> None:
    """Register core application services."""

    # ConfigurationManager is now registered earlier in lazy mode
    # Only register it here if not already registered (eager mode)
    if not container.has(ConfigurationManager):

        def create_configuration_manager(c):
            return ConfigurationManager()  # Uses default config discovery

        container.register_singleton(ConfigurationManager, create_configuration_manager)

    # Register metrics collector with configuration from ConfigurationPort
    def create_metrics_collector(c):
        config_port = c.get(ConfigurationPort)
        metrics_config = config_port.get_metrics_config()
        return MetricsCollector(metrics_config, logger=c.get(LoggingPort))

    # Register as singleton so the same collector instance is shared
    container.register_singleton(MetricsCollector, create_metrics_collector)

    # Register factories
    from orb.infrastructure.scheduler.factory import SchedulerStrategyFactory
    from orb.infrastructure.storage.factory import StorageStrategyFactory

    container.register_factory(
        SchedulerStrategyFactory, lambda c: SchedulerStrategyFactory(c.get(ConfigurationManager))
    )
    container.register_factory(
        StorageStrategyFactory, lambda c: StorageStrategyFactory(c.get(ConfigurationManager))
    )

    # Register template format converter

    # Register scheduler strategy
    container.register_factory(SchedulerPort, _create_scheduler_strategy)

    # Register storage strategy
    container.register_factory(StoragePort, _create_storage_strategy)

    # Register provider strategy
    container.register_factory(ProviderDiscoveryPort, _create_provider_strategy)

    # Register EventBus as singleton so all repositories and subscribers share one instance
    from orb.application.events.bus.event_bus import EventBus
    from orb.application.events.handlers.metrics_event_handler import MetricsEventHandler

    def create_event_bus(c: DIContainer) -> EventBus:
        bus = EventBus(logger=c.get(LoggingPort))
        collector = c.get(MetricsCollector)
        handler = MetricsEventHandler(collector=collector, logger=c.get(LoggingPort))
        bus.register_handler("RequestCreatedEvent", handler)
        bus.register_handler("RequestCompletedEvent", handler)
        bus.register_handler("RequestFailedEvent", handler)
        return bus

    container.register_singleton(EventBus, create_event_bus)

    # Register event publisher
    from orb.infrastructure.events.publisher import ConfigurableEventPublisher

    container.register_factory(
        EventPublisherPort,
        lambda c: ConfigurableEventPublisher(mode="logging"),  # Default to logging mode
    )

    # Register command and query buses conditionally based on lazy loading mode
    # If lazy loading is enabled, buses will be registered by CQRS handler discovery
    # If lazy loading is disabled, register them as factories immediately
    if not container.is_lazy_loading_enabled():
        container.register_factory(
            CommandBus,
            lambda c: CommandBus(container=c, logger=c.get(LoggingPort)),  # type: ignore[call-arg]
        )
        container.register_factory(
            QueryBus,
            lambda c: QueryBus(container=c, logger=c.get(LoggingPort)),  # type: ignore[call-arg]
        )

    # Register native spec service
    def create_native_spec_service(c):
        """Create native spec service."""
        from orb.application.services.native_spec_service import NativeSpecService
        from orb.domain.base.ports.spec_rendering_port import SpecRenderingPort

        return NativeSpecService(  # type: ignore[call-arg]
            config_port=c.get(ConfigurationPort), spec_renderer=c.get(SpecRenderingPort)
        )

    from orb.application.services.native_spec_service import NativeSpecService

    container.register_factory(NativeSpecService, create_native_spec_service)


def _create_scheduler_strategy(container: "DIContainer") -> SchedulerPort:
    """Create scheduler strategy using factory."""
    from orb.infrastructure.scheduler.factory import SchedulerStrategyFactory

    factory = container.get(SchedulerStrategyFactory)
    config = container.get(ConfigurationPort)
    scheduler_type = config.get_scheduler_strategy()
    return factory.create_strategy(scheduler_type, container)


def _create_storage_strategy(container: "DIContainer") -> StoragePort:
    """Create storage strategy using factory."""
    from orb.infrastructure.storage.factory import StorageStrategyFactory

    factory = container.get(StorageStrategyFactory)
    config = container.get(ConfigurationPort)
    storage_type = config.get_storage_strategy()
    return factory.create_strategy(storage_type, config)


def _create_provider_strategy(container: "DIContainer") -> ProviderDiscoveryPort:
    """Create provider strategy using registry pattern."""
    from orb.domain.base.ports.logging_port import LoggingPort
    from orb.infrastructure.adapters.provider_discovery_adapter import ProviderDiscoveryAdapter
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()
    return ProviderDiscoveryAdapter(registry, logger=container.get(LoggingPort))

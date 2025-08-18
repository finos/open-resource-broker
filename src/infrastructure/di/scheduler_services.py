"""Scheduler service registrations for dependency injection."""

from domain.base.ports import ConfigurationPort
from infrastructure.di.container import DIContainer
from infrastructure.factories.scheduler_strategy_factory import SchedulerStrategyFactory
from infrastructure.logging.logger import get_logger


def register_scheduler_services(container: DIContainer) -> None:
    """Register scheduler services with configuration-driven strategy loading."""

    # Register scheduler strategy factory
    container.register_factory(SchedulerStrategyFactory, create_scheduler_strategy_factory)

    # Register only the configured scheduler strategy
    _register_configured_scheduler_strategy(container)


def create_scheduler_strategy_factory(container: DIContainer) -> SchedulerStrategyFactory:
    """Create scheduler strategy factory with configuration."""
    config = container.get(ConfigurationPort)
    return SchedulerStrategyFactory(config_manager=config)


def _register_configured_scheduler_strategy(container: DIContainer) -> None:
    """Register only the configured scheduler strategy."""
    try:
        config = container.get(ConfigurationPort)
        scheduler_type = config.get_scheduler_strategy()

        logger = get_logger(__name__)

        # Registry handles dynamic registration - no hardcoded types here
        from infrastructure.registry.scheduler_registry import get_scheduler_registry

        registry = get_scheduler_registry()
        registry.ensure_type_registered(scheduler_type)

        logger.info("Registered configured scheduler strategy: %s", scheduler_type)

    except Exception as e:
        logger = get_logger(__name__)
        logger.error("Failed to register configured scheduler strategy: %s", e)
        # Fallback to default
        from infrastructure.registry.scheduler_registry import get_scheduler_registry

        registry = get_scheduler_registry()
        registry.ensure_type_registered("default")
        logger.info("Registered fallback scheduler strategy: default")

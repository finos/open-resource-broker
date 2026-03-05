"""Scheduler service registrations for dependency injection."""

from domain.base.ports import ConfigurationPort
from infrastructure.di.container import DIContainer
from infrastructure.logging.logger import get_logger
from infrastructure.scheduler.factory import SchedulerStrategyFactory


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

    # Make this lazy too - don't access config during registration
    def lazy_register():
        try:
            config = container.get(ConfigurationPort)
            scheduler_type = config.get_scheduler_strategy()

            logger = get_logger(__name__)

            # Registry handles dynamic registration - no hardcoded types here
            from infrastructure.scheduler.registry import get_scheduler_registry

            registry = get_scheduler_registry()
            registry.ensure_type_registered(scheduler_type)

            logger.info("Registered configured scheduler strategy: %s", scheduler_type)

        except Exception as e:
            logger = get_logger(__name__)
            logger.error("Failed to register configured scheduler strategy: %s", e, exc_info=True)
            # Fallback to default
            from infrastructure.scheduler.registry import get_scheduler_registry

            registry = get_scheduler_registry()
            registry.ensure_type_registered("default")

    # Don't call lazy_register() here - let it be called when needed
    # For now, just register the default to avoid issues
    from infrastructure.scheduler.registry import get_scheduler_registry

    registry = get_scheduler_registry()
    registry.ensure_type_registered("default")

    logger = get_logger(__name__)
    logger.info("Registered fallback scheduler strategy: default")

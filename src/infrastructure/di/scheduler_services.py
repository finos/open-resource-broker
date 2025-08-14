"""Scheduler service registrations for dependency injection."""

from src.domain.base.ports import ConfigurationPort, LoggingPort
from src.infrastructure.di.container import DIContainer
from src.infrastructure.factories.scheduler_strategy_factory import (
    SchedulerStrategyFactory,
)
from src.infrastructure.logging.logger import get_logger


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
        scheduler_type = config.get_scheduler_strategy()  # Defaults to "default"
        
        logger = get_logger(__name__)
        
        # Register only the configured scheduler type
        if scheduler_type in ["hostfactory", "hf"]:
            from src.infrastructure.scheduler.registration import register_symphony_hostfactory_scheduler
            register_symphony_hostfactory_scheduler()
            logger.info(f"Registered configured scheduler strategy: {scheduler_type}")
        elif scheduler_type == "default":
            from src.infrastructure.scheduler.registration import register_default_scheduler
            register_default_scheduler()
            logger.info(f"Registered configured scheduler strategy: {scheduler_type}")
        else:
            logger.warning(f"Unknown scheduler strategy: {scheduler_type}, falling back to default")
            from src.infrastructure.scheduler.registration import register_default_scheduler
            register_default_scheduler()
            logger.info("Registered configured scheduler strategy: default (fallback)")
            
    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"Failed to register configured scheduler strategy: {e}")
        # Fallback to default
        from src.infrastructure.scheduler.registration import register_default_scheduler
        register_default_scheduler()
        logger.info("Registered configured scheduler strategy: default (fallback)")

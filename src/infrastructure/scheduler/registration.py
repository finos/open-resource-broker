"""Scheduler registration functions - same pattern as provider registration."""

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from src.domain.base.ports.scheduler_port import SchedulerPort
    from src.infrastructure.di.container import DIContainer
    from src.infrastructure.registry.scheduler_registry import SchedulerRegistry


def create_symphony_hostfactory_strategy(container: "DIContainer") -> "SchedulerPort":
    """Create Symphony HostFactory scheduler strategy.

    Args:
        container: Dependency injection container

    Returns:
        SchedulerPort: Symphony HostFactory scheduler strategy instance
    """
    from src.config.manager import ConfigurationManager
    from src.domain.base.ports import LoggingPort
    from src.infrastructure.scheduler.hostfactory.strategy import (
        HostFactorySchedulerStrategy,
    )

    config_manager = container.get(ConfigurationManager)
    logger = container.get(LoggingPort)
    return HostFactorySchedulerStrategy(config_manager, logger)


def create_hostfactory_config(data: Dict[str, Any]) -> Any:
    """Create HostFactory scheduler configuration."""
    return data


def register_symphony_hostfactory_scheduler(registry: "SchedulerRegistry" = None):
    """Register Symphony HostFactory scheduler."""
    if registry is None:
        from src.infrastructure.registry.scheduler_registry import (
            get_scheduler_registry,
        )

        registry = get_scheduler_registry()

    try:
        registry.register(
            scheduler_type="hostfactory",
            strategy_factory=create_symphony_hostfactory_strategy,
            config_factory=create_hostfactory_config,
        )

        # Also register with 'hf' alias
        registry.register(
            scheduler_type="hf",
            strategy_factory=create_symphony_hostfactory_strategy,
            config_factory=create_hostfactory_config,
        )
    except ValueError as e:
        # Ignore if already registered (idempotent registration)
        if "already registered" in str(e):
            from src.infrastructure.logging.logger import get_logger

            logger = get_logger(__name__)
            logger.debug(f"Scheduler types already registered: {str(e)}")
        else:
            raise
    except Exception as e:
        from src.infrastructure.logging.logger import get_logger

        logger = get_logger(__name__)
        logger.error(f"Failed to register Symphony HostFactory scheduler: {str(e)}")
        raise


def create_default_strategy(container: "DIContainer") -> "SchedulerPort":
    """Create default scheduler strategy.

    Args:
        container: Dependency injection container

    Returns:
        SchedulerPort: Default scheduler strategy instance
    """
    from src.config.manager import ConfigurationManager
    from src.domain.base.ports import LoggingPort
    from src.infrastructure.scheduler.default.strategy import DefaultSchedulerStrategy

    config_manager = container.get(ConfigurationManager)
    logger = container.get(LoggingPort)
    return DefaultSchedulerStrategy(config_manager, logger)


def create_default_config(data: Dict[str, Any]) -> Any:
    """Create default scheduler configuration."""
    return data


def register_default_scheduler(registry: "SchedulerRegistry" = None):
    """Register default scheduler."""
    if registry is None:
        from src.infrastructure.registry.scheduler_registry import (
            get_scheduler_registry,
        )

        registry = get_scheduler_registry()

    try:
        registry.register(
            scheduler_type="default",
            strategy_factory=create_default_strategy,
            config_factory=create_default_config,
        )
    except ValueError as e:
        # Ignore if already registered (idempotent registration)
        if "already registered" in str(e):
            from src.infrastructure.logging.logger import get_logger

            logger = get_logger(__name__)
            logger.debug(f"Default scheduler already registered: {str(e)}")
        else:
            raise
    except Exception as e:
        from src.infrastructure.logging.logger import get_logger

        logger = get_logger(__name__)
        logger.error(f"Failed to register default scheduler: {str(e)}")
        raise


def register_all_scheduler_types():
    """Register all scheduler types - same pattern as storage/provider registration."""
    register_symphony_hostfactory_scheduler()
    register_default_scheduler()


def register_active_scheduler_only(scheduler_type: str = "default") -> bool:
    """
    Register only the active scheduler type for faster startup .

    Args:
        scheduler_type: Type of scheduler to register ("hostfactory", "hf", or "default")

    Returns:
        True if registration was successful, False otherwise
    """
    from src.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    try:
        if scheduler_type in ["hostfactory", "hf"]:
            register_symphony_hostfactory_scheduler()
            logger.info(f"Registered active scheduler: {scheduler_type}")
        elif scheduler_type == "default":
            register_default_scheduler()
            logger.info(f"Registered active scheduler: {scheduler_type}")
        else:
            logger.warning(
                f"Unknown scheduler type: {scheduler_type}, falling back to default")
            register_default_scheduler()
            logger.info("Registered active scheduler: default (fallback)")

        return True

    except Exception as e:
        logger.error(f"Failed to register active scheduler '{scheduler_type}': {e}")
        return False


def register_scheduler_on_demand(scheduler_type: str) -> bool:
    """
    Register a specific scheduler type on demand .

    Args:
        scheduler_type: Name of the scheduler type to register

    Returns:
        True if registration was successful, False otherwise
    """
    from src.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    try:
        if scheduler_type in ["hostfactory", "hf"]:
            register_symphony_hostfactory_scheduler()
        elif scheduler_type == "default":
            register_default_scheduler()
        else:
            logger.error(f"Unknown scheduler type: {scheduler_type}")
            return False

        logger.info(
            f"Successfully registered scheduler type on demand: {scheduler_type}")
        return True

    except Exception as e:
        logger.error(
            f"Failed to register scheduler type '{scheduler_type}' on demand: {e}")
        return False

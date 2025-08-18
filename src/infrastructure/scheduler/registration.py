"""Scheduler strategy registration and factory functions.

This module provides registration functions for different scheduler strategies:
- HostFactory scheduler for IBM Symphony compatibility
- Default scheduler for native domain format
- Strategy factory functions with dependency injection
- Registry management for scheduler types
"""

from typing import TYPE_CHECKING, Any, Dict

from domain.base.ports.configuration_port import ConfigurationPort

if TYPE_CHECKING:
    from domain.base.ports.scheduler_port import SchedulerPort
    from infrastructure.di.container import DIContainer
    from infrastructure.registry.scheduler_registry import SchedulerRegistry


def create_symphony_hostfactory_strategy(container: "DIContainer") -> "SchedulerPort":
    """Create Symphony HostFactory scheduler strategy.

    Args:
        container: Dependency injection container

    Returns:
        SchedulerPort: Symphony HostFactory scheduler strategy instance
    """
    from domain.base.ports import LoggingPort
    from infrastructure.scheduler.hostfactory.strategy import (
        HostFactorySchedulerStrategy,
    )

    config_manager = container.get(ConfigurationPort)
    logger = container.get(LoggingPort)
    return HostFactorySchedulerStrategy(config_manager, logger)


def create_hostfactory_config(data: Dict[str, Any]) -> Any:
    """Create HostFactory scheduler configuration."""
    return data


def register_symphony_hostfactory_scheduler(registry: "SchedulerRegistry" = None):
    """Register Symphony HostFactory scheduler."""
    if registry is None:
        from infrastructure.registry.scheduler_registry import get_scheduler_registry

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
            from infrastructure.logging.logger import get_logger

            logger = get_logger(__name__)
            logger.debug("Scheduler types already registered: %s", str(e))
        else:
            raise
    except Exception as e:
        from infrastructure.logging.logger import get_logger

        logger = get_logger(__name__)
        logger.error("Failed to register Symphony HostFactory scheduler: %s", str(e))
        raise


def create_default_strategy(container: "DIContainer") -> "SchedulerPort":
    """Create default scheduler strategy.

    Args:
        container: Dependency injection container

    Returns:
        SchedulerPort: Default scheduler strategy instance
    """
    from domain.base.ports import LoggingPort
    from infrastructure.scheduler.default.strategy import DefaultSchedulerStrategy

    config_manager = container.get(ConfigurationPort)
    logger = container.get(LoggingPort)
    return DefaultSchedulerStrategy(config_manager, logger)


def create_default_config(data: Dict[str, Any]) -> Any:
    """Create default scheduler configuration."""
    return data


def register_default_scheduler(registry: "SchedulerRegistry" = None):
    """Register default scheduler."""
    if registry is None:
        from infrastructure.registry.scheduler_registry import get_scheduler_registry

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
            from infrastructure.logging.logger import get_logger

            logger = get_logger(__name__)
            logger.debug("Default scheduler already registered: %s", str(e))
        else:
            raise
    except Exception as e:
        from infrastructure.logging.logger import get_logger

        logger = get_logger(__name__)
        logger.error("Failed to register default scheduler: %s", str(e))
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
    from infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    try:
        if scheduler_type in ["hostfactory", "hf"]:
            register_symphony_hostfactory_scheduler()
            logger.info("Registered active scheduler: %s", scheduler_type)
        elif scheduler_type == "default":
            register_default_scheduler()
            logger.info("Registered active scheduler: %s", scheduler_type)
        else:
            logger.warning("Unknown scheduler type: %s, falling back to default", scheduler_type)
            register_default_scheduler()
            logger.info("Registered active scheduler: default (fallback)")

        return True

    except Exception as e:
        logger.error("Failed to register active scheduler '%s': %s", scheduler_type, e)
        return False


def register_scheduler_on_demand(scheduler_type: str) -> bool:
    """
    Register a specific scheduler type on demand .

    Args:
        scheduler_type: Name of the scheduler type to register

    Returns:
        True if registration was successful, False otherwise
    """
    from infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    try:
        if scheduler_type in ["hostfactory", "hf"]:
            register_symphony_hostfactory_scheduler()
        elif scheduler_type == "default":
            register_default_scheduler()
        else:
            logger.error("Unknown scheduler type: %s", scheduler_type)
            return False

        logger.info("Successfully registered scheduler type on demand: %s", scheduler_type)
        return True

    except Exception as e:
        logger.error("Failed to register scheduler type '%s' on demand: %s", scheduler_type, e)
        return False

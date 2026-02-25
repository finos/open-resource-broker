"""Scheduler strategy registration and factory functions.

This module provides registration functions for different scheduler strategies:
- HostFactory scheduler for IBM Symphony compatibility
- Default scheduler for native domain format
- Strategy factory functions with dependency injection
- Registry management for scheduler types
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from domain.base.ports.scheduler_port import SchedulerPort
    from infrastructure.scheduler.registry import SchedulerRegistry


def create_symphony_hostfactory_strategy(config: Any) -> "SchedulerPort":
    """Create Symphony HostFactory scheduler strategy.

    Args:
        config: Scheduler configuration

    Returns:
        SchedulerPort: Symphony HostFactory scheduler strategy instance
    """
    from domain.template.ports.template_defaults_port import TemplateDefaultsPort
    from infrastructure.di.container import get_container, is_container_ready
    from infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

    template_defaults_service = None
    if is_container_ready():
        template_defaults_service = get_container().get_optional(TemplateDefaultsPort)

    return HostFactorySchedulerStrategy(template_defaults_service=template_defaults_service)


def create_hostfactory_config(data: dict[str, Any]) -> Any:
    """Create HostFactory scheduler configuration."""
    return data


def register_symphony_hostfactory_scheduler(
    registry: "SchedulerRegistry | None" = None,
) -> None:
    """Register Symphony HostFactory scheduler (idempotent)."""
    if registry is None:
        from infrastructure.scheduler.registry import get_scheduler_registry

        registry = get_scheduler_registry()

    # Registry handles idempotent registration
    registry.register(
        type_name="hostfactory",
        strategy_factory=create_symphony_hostfactory_strategy,
        config_factory=create_hostfactory_config,
    )

    # Also register with 'hf' alias
    registry.register(
        type_name="hf",
        strategy_factory=create_symphony_hostfactory_strategy,
        config_factory=create_hostfactory_config,
    )


def create_default_strategy(config: Any) -> "SchedulerPort":
    """Create default scheduler strategy.

    Args:
        config: Scheduler configuration

    Returns:
        SchedulerPort: Default scheduler strategy instance
    """
    from domain.template.ports.template_defaults_port import TemplateDefaultsPort
    from infrastructure.di.container import get_container, is_container_ready
    from infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    template_defaults_service = None
    if is_container_ready():
        template_defaults_service = get_container().get_optional(TemplateDefaultsPort)

    return DefaultSchedulerStrategy(template_defaults_service=template_defaults_service)


def create_default_config(data: dict[str, Any]) -> Any:
    """Create default scheduler configuration."""
    return data


def register_default_scheduler(registry: "SchedulerRegistry | None" = None) -> None:
    """Register default scheduler (idempotent)."""
    if registry is None:
        from infrastructure.scheduler.registry import get_scheduler_registry

        registry = get_scheduler_registry()

    # Registry handles idempotent registration
    registry.register(
        type_name="default",
        strategy_factory=create_default_strategy,
        config_factory=create_default_config,
    )


def register_all_scheduler_types() -> None:
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
        logger.error(
            "Failed to register active scheduler '%s': %s", scheduler_type, e, exc_info=True
        )
        return False

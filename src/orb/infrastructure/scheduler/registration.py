"""Scheduler strategy registration and factory functions.

This module provides registration functions for different scheduler strategies:
- HostFactory scheduler for IBM Symphony compatibility
- Default scheduler for native domain format
- Strategy factory functions with dependency injection
- Registry management for scheduler types
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orb.domain.base.ports.scheduler_port import SchedulerPort
    from orb.infrastructure.scheduler.registry import SchedulerRegistry


def create_symphony_hostfactory_strategy(config: Any) -> "SchedulerPort":
    """Create Symphony HostFactory scheduler strategy.

    Args:
        config: Scheduler configuration — either a DI container (during normal
            startup) or a plain dict (non-container call paths).

    Returns:
        SchedulerPort: Symphony HostFactory scheduler strategy instance
    """
    from orb.application.services.provider_registry_service import ProviderRegistryService
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.domain.base.ports.logging_port import LoggingPort
    from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort
    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

    template_defaults_service = None
    config_port = None
    logger = None
    provider_registry_service = None

    if hasattr(config, "get_optional"):
        template_defaults_service = config.get_optional(TemplateDefaultsPort)
        config_port = config.get_optional(ConfigurationPort)
        logger = config.get_optional(LoggingPort)
        provider_registry_service = config.get_optional(ProviderRegistryService)

    return HostFactorySchedulerStrategy(
        template_defaults_service=template_defaults_service,
        config_port=config_port,
        logger=logger,
        provider_registry_service=provider_registry_service,
    )


def create_hostfactory_config(data: dict[str, Any]) -> Any:
    """Create HostFactory scheduler configuration."""
    return data


def register_symphony_hostfactory_scheduler(
    registry: "SchedulerRegistry | None" = None,
) -> None:
    """Register Symphony HostFactory scheduler (idempotent)."""
    if registry is None:
        from orb.infrastructure.scheduler.registry import get_scheduler_registry

        registry = get_scheduler_registry()

    from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

    # Registry handles idempotent registration
    registry.register(
        type_name="hostfactory",
        strategy_factory=create_symphony_hostfactory_strategy,
        config_factory=create_hostfactory_config,
        strategy_class=HostFactorySchedulerStrategy,
    )


def create_default_strategy(config: Any) -> "SchedulerPort":
    """Create default scheduler strategy.

    Args:
        config: Scheduler configuration — either a DI container (during normal
            startup) or a plain dict (non-container call paths).

    Returns:
        SchedulerPort: Default scheduler strategy instance
    """
    from orb.application.services.provider_registry_service import ProviderRegistryService
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.domain.base.ports.logging_port import LoggingPort
    from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    template_defaults_service = None
    config_port = None
    logger = None
    provider_registry_service = None

    if hasattr(config, "get_optional"):
        template_defaults_service = config.get_optional(TemplateDefaultsPort)
        config_port = config.get_optional(ConfigurationPort)
        logger = config.get_optional(LoggingPort)
        provider_registry_service = config.get_optional(ProviderRegistryService)

    return DefaultSchedulerStrategy(
        template_defaults_service=template_defaults_service,
        config_port=config_port,
        logger=logger,
        provider_registry_service=provider_registry_service,
    )


def create_default_config(data: dict[str, Any]) -> Any:
    """Create default scheduler configuration."""
    return data


def register_default_scheduler(registry: "SchedulerRegistry | None" = None) -> None:
    """Register default scheduler (idempotent)."""
    if registry is None:
        from orb.infrastructure.scheduler.registry import get_scheduler_registry

        registry = get_scheduler_registry()

    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    # Registry handles idempotent registration
    registry.register(
        type_name="default",
        strategy_factory=create_default_strategy,
        config_factory=create_default_config,
        strategy_class=DefaultSchedulerStrategy,
    )


def register_all_scheduler_types() -> None:
    """Register all scheduler types - same pattern as storage/provider registration."""
    register_symphony_hostfactory_scheduler()
    register_default_scheduler()


def register_active_scheduler_only(scheduler_type: str = "default") -> bool:
    """
    Register only the active scheduler type for faster startup.

    Uses the registry to look up and invoke the correct registration function,
    avoiding hardcoded if/elif dispatch.

    Args:
        scheduler_type: Type of scheduler to register ("hostfactory" or "default")

    Returns:
        True if registration was successful, False otherwise
    """
    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    # Registry mapping: type name -> registration function
    registration_functions: dict[str, Any] = {
        "hostfactory": register_symphony_hostfactory_scheduler,
        "default": register_default_scheduler,
    }

    try:
        register_fn = registration_functions.get(scheduler_type)
        if register_fn is not None:
            register_fn()
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

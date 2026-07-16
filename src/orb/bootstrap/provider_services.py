"""Provider service registrations for dependency injection."""

from orb.infrastructure.di.container import DIContainer
from orb.infrastructure.logging.logger import get_logger


def register_provider_services(container: DIContainer) -> None:
    """Register provider application services and utilities."""

    # Register enhanced application services
    _register_application_services(container)

    # Register provider-specific utility services
    _register_provider_utility_services(container)


def _register_application_services(container: DIContainer) -> None:
    """Register enhanced application services with proper dependencies."""
    from orb.application.services.machine_sync_service import MachineSyncService
    from orb.application.services.provider_registry_service import ProviderRegistryService
    from orb.domain.base.ports.logging_port import LoggingPort
    from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
    from orb.domain.services.template_validation_domain_service import (
        TemplateValidationDomainService,
    )
    from orb.infrastructure.di.buses import CommandBus
    from orb.providers.registry import get_provider_registry

    def _create_provider_registry(c):
        from orb.domain.base.ports.configuration_port import ConfigurationPort
        from orb.infrastructure.services.provider_selection_service import ProviderSelectionService

        registry = get_provider_registry()
        config_port = c.get(ConfigurationPort)
        registry._config_port = config_port
        # Wire the selection service so the registry delegates to it rather than
        # constructing a bare instance lazily (ensures the same config_port is used).
        registry._selection_service = ProviderSelectionService(
            registry=registry, config_port=config_port
        )
        return registry

    container.register_singleton(ProviderRegistryPort, _create_provider_registry)

    def create_provider_registry_service(c):
        registry = c.get(ProviderRegistryPort)
        validation_service = c.get(TemplateValidationDomainService)
        logger = c.get(LoggingPort)
        return ProviderRegistryService(registry, validation_service, logger)

    container.register_singleton(ProviderRegistryService, create_provider_registry_service)

    # Machine sync service - lazy initialization
    def create_machine_sync_service(c):
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.domain.base import UnitOfWorkFactory
        from orb.domain.base.ports.configuration_port import ConfigurationPort

        command_bus = c.get(CommandBus)
        uow_factory = c.get(UnitOfWorkFactory)
        config_port = c.get(ConfigurationPort)
        logger = c.get(LoggingPort)
        provider_registry_service = c.get(ProviderRegistryService)
        return MachineSyncService(
            command_bus, uow_factory, config_port, logger, provider_registry_service
        )

    container.register_singleton(MachineSyncService, create_machine_sync_service)


def _register_provider_utility_services(container: DIContainer) -> None:
    """Register provider-specific utility services for all registered providers."""
    import importlib
    import importlib.util

    from orb.providers.registry import get_provider_registry

    logger = get_logger(__name__)

    registered_types = get_provider_registry().get_registered_types()
    if not registered_types:
        logger.warning(
            "No providers registered when wiring utility services; "
            "ensure entry-points are installed (run 'uv sync' or 'pip install -e .')."
        )

    for name in registered_types:
        mod_path = f"orb.providers.{name}.registration"
        try:
            spec = importlib.util.find_spec(mod_path)
        except (ImportError, ValueError) as e:
            logger.debug("%s find_spec raised %s; skipping", name, type(e).__name__)
            continue
        if spec is None:
            logger.debug("%s provider not available, skipping utility service registration", name)
            continue
        try:
            mod = importlib.import_module(mod_path)

            # Call initialize_<name>_provider to populate satellite registries
            # (CLISpecRegistry, FieldMappingRegistry, DefaultsLoaderRegistry,
            # ProviderSettingsRegistry, TemplateExtensionRegistry).  We pass
            # template_factory=None here; the DI services step below wires the
            # template adapter separately once TemplateFactory is available.
            init_fn = getattr(mod, f"initialize_{name}_provider", None)
            if init_fn is not None:
                init_fn(template_factory=None, logger=None)
                logger.debug("%s satellite registries populated via initialize", name)

            # Register DI utility services (e.g. AWS template adapter, clients)
            di_fn = getattr(mod, f"register_{name}_services_with_di", None)
            if di_fn is not None:
                di_fn(container)
                logger.debug("%s utility services registered with DI", name)

            # Register auth strategies so the auth registry can resolve them
            # without server.py importing provider classes directly.
            # Pass None for the logging port; registration is best-effort and
            # the bootstrap logger (ContextLogger) does not implement LoggingPort.
            auth_fn = getattr(mod, f"register_{name}_auth_strategies", None)
            if auth_fn is not None:
                auth_fn(None)
                logger.debug("%s auth strategies registered", name)

        except Exception as e:
            logger.warning("Failed to register %s utility services: %s", name, str(e))

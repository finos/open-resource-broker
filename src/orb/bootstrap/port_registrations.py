"""Port adapter registrations for dependency injection."""

from orb.config.managers.configuration_manager import ConfigurationManager
from orb.domain.base.ports import (
    ConfigurationPort,
    ContainerPort,
    ErrorHandlingPort,
    ProviderConfigPort,
    ProviderSelectionPort,
    TemplateConfigurationPort,
)
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.spec_rendering_port import SpecRenderingPort
from orb.infrastructure.adapters.error_handling_adapter import ErrorHandlingAdapter
from orb.infrastructure.adapters.factories.container_adapter_factory import (
    ContainerAdapterFactory,
)
from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager


def register_port_adapters(container):
    """Register all port adapters in the DI container."""

    # Register configuration port with adapter
    def create_configuration_adapter(container):
        """Create configuration adapter using DI-managed ConfigurationManager."""
        from orb.infrastructure.adapters.configuration_adapter import ConfigurationAdapter

        config_manager = container.get(ConfigurationManager)  # Use DI instance
        return ConfigurationAdapter(config_manager, container.get(LoggingPort))

    container.register_singleton(ConfigurationPort, create_configuration_adapter)

    # Register focused ProviderConfigPort - reuse the same ConfigurationAdapter
    # since ConfigurationPort extends ProviderConfigPort (DIP: depend on abstraction)
    container.register_singleton(ProviderConfigPort, lambda c: c.get(ConfigurationPort))

    # Register UnitOfWorkFactory (abstract -> concrete mapping)
    # This was previously in _setup_core_dependencies but got lost during DI cleanup
    # Using consistent Base* naming pattern for abstract classes
    def create_unit_of_work_factory(c):
        from orb.infrastructure.utilities.factories.repository_factory import UnitOfWorkFactory

        config_manager = c.get(ConfigurationManager)
        return UnitOfWorkFactory(config_manager, LoggingAdapter("unit_of_work"))

    from orb.domain.base import UnitOfWorkFactory as BaseUnitOfWorkFactory

    container.register_singleton(BaseUnitOfWorkFactory, create_unit_of_work_factory)

    # Register logging port adapter
    container.register_singleton(LoggingPort, lambda c: LoggingAdapter("application"))

    # Register container port adapter using factory to avoid circular dependency
    container.register_singleton(ContainerPort, lambda c: ContainerAdapterFactory.create_adapter(c))

    # Register error handling port adapter
    container.register_singleton(ErrorHandlingAdapter, lambda c: ErrorHandlingAdapter())
    container.register_singleton(ErrorHandlingPort, lambda c: c.get(ErrorHandlingAdapter))

    # Register template configuration port adapter
    from orb.infrastructure.adapters.template_configuration_adapter import (
        TemplateConfigurationAdapter,
    )

    container.register_singleton(
        TemplateConfigurationAdapter,
        lambda c: TemplateConfigurationAdapter(
            template_manager=c.get(TemplateConfigurationManager),
            logger=c.get(LoggingPort),
        ),
    )
    container.register_singleton(
        TemplateConfigurationPort, lambda c: c.get(TemplateConfigurationAdapter)
    )

    # Register spec rendering port
    def create_spec_renderer(c):
        """Create Jinja spec renderer."""
        from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer

        return JinjaSpecRenderer(logger=c.get(LoggingPort))

    container.register_singleton(SpecRenderingPort, create_spec_renderer)

    # Register provider selection port adapter
    def create_provider_selection_adapter(c):
        """Create provider selection adapter wrapping ProviderRegistryService."""
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.infrastructure.adapters.provider_selection_adapter import ProviderSelectionAdapter

        provider_registry_service = c.get(ProviderRegistryService)
        return ProviderSelectionAdapter(provider_registry_service)

    container.register_singleton(ProviderSelectionPort, create_provider_selection_adapter)

    # Register path resolution port adapter
    from orb.domain.base.ports.path_resolution_port import PathResolutionPort
    from orb.infrastructure.adapters.path_resolution_adapter import PathResolutionAdapter

    container.register_singleton(PathResolutionPort, lambda c: PathResolutionAdapter())

    # Register in-memory cache service as CacheServicePort implementation.
    # The handler (GetRequestHandler) calls only the sync convenience methods
    # (get_cached_request / cache_request / is_caching_enabled).
    # TODO: replace with a config-driven implementation (Redis etc.) when needed.
    from orb.application.ports.cache_service_port import CacheServicePort
    from orb.infrastructure.caching.in_memory_cache_service import InMemoryCacheService

    container.register_singleton(CacheServicePort, lambda _: InMemoryCacheService())

    # Register console port adapter
    from orb.domain.base.ports.console_port import ConsolePort
    from orb.infrastructure.adapters.console_adapter import RichConsoleAdapter

    container.register_singleton(ConsolePort, lambda c: RichConsoleAdapter())

    # Register response formatting service
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.interface.response_formatting_service import ResponseFormattingService

    container.register_singleton(
        ResponseFormattingService,
        lambda c: ResponseFormattingService(c.get(SchedulerPort)),
    )

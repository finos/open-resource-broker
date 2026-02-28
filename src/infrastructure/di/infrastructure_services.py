"""Infrastructure service registrations for dependency injection."""

from domain.base.ports.configuration_port import ConfigurationPort
from domain.base.ports.logging_port import LoggingPort
from domain.machine.repository import MachineRepository
from domain.request.repository import RequestRepository
from domain.template.repository import TemplateRepository
from infrastructure.di.container import DIContainer
from infrastructure.template.configuration_manager import TemplateConfigurationManager


def register_infrastructure_services(container: DIContainer) -> None:
    """Register infrastructure services."""

    # Register template services
    _register_template_services(container)

    # Register repository services
    _register_repository_services(container)

    # Register caching services
    _register_caching_services(container)


def _register_template_services(container: DIContainer):
    """Register template configuration services."""

    # Register template defaults port with inline factory
    def create_template_defaults_service(c):
        """Create template defaults service with injected dependencies."""
        from application.services.template_defaults_service import (
            TemplateDefaultsService,
        )

        return TemplateDefaultsService(
            config_manager=c.get(ConfigurationPort),
            logger=c.get(LoggingPort),
        )

    from domain.template.ports.template_defaults_port import TemplateDefaultsPort

    container.register_singleton(TemplateDefaultsPort, create_template_defaults_service)

    # Register template generation service
    def create_template_generation_service(c):
        """Create template generation service with injected dependencies."""
        from application.services.provider_registry_service import ProviderRegistryService
        from application.services.template_generation_service import (
            TemplateGenerationService,
        )
        from domain.base.ports.scheduler_port import SchedulerPort
        from domain.base.ports.template_example_generator_port import TemplateExampleGeneratorPort

        return TemplateGenerationService(
            config_manager=c.get(ConfigurationPort),
            scheduler_strategy=c.get(SchedulerPort),
            logger=c.get(LoggingPort),
            provider_registry_service=c.get(ProviderRegistryService),
            template_example_generator=c.get(TemplateExampleGeneratorPort),
        )

    from application.services.template_generation_service import TemplateGenerationService

    container.register_singleton(TemplateGenerationService, create_template_generation_service)

    # Register template configuration manager with factory function
    def create_template_configuration_manager(
        c: DIContainer,
    ) -> TemplateConfigurationManager:
        """Create TemplateConfigurationManager."""
        from application.services.provider_registry_service import ProviderRegistryService
        from config.managers.configuration_manager import ConfigurationManager
        from domain.base.ports.scheduler_port import SchedulerPort
        from domain.template.factory import TemplateFactory
        from domain.template.ports.template_defaults_port import TemplateDefaultsPort

        return TemplateConfigurationManager(
            config_manager=c.get(ConfigurationManager),
            scheduler_strategy=c.get(SchedulerPort),
            logger=c.get(LoggingPort),
            event_publisher=None,
            template_defaults_service=c.get(TemplateDefaultsPort),  # type: ignore[arg-type]
            provider_registry_service=c.get(ProviderRegistryService),
            template_factory=TemplateFactory(logger=c.get(LoggingPort)),
        )

    container.register_singleton(
        TemplateConfigurationManager, create_template_configuration_manager
    )

    # Check if AMI resolution is enabled via AWS extensions
    _register_ami_resolver_if_enabled(container)


def _register_ami_resolver_if_enabled(container: DIContainer) -> None:
    """Register AMI resolver when implemented.

    TODO: CachingAMIResolver is not yet implemented. When ready, check
    TemplateExtensionRegistry for AWS AMI resolution config and register
    the resolver against TemplateResolverPort.
    """


def _register_repository_services(container: DIContainer) -> None:
    """Register repository services."""
    from infrastructure.template.configuration_manager import (
        TemplateConfigurationManager,
    )
    from infrastructure.template.template_repository_impl import (
        create_template_repository_impl,
    )
    from infrastructure.utilities.factories.repository_factory import RepositoryFactory

    # Storage strategies are now registered by storage_services.py
    # No need to register them here anymore
    # Register repository factory
    container.register_singleton(RepositoryFactory)

    # Register repositories
    container.register_singleton(
        RequestRepository,
        lambda c: c.get(RepositoryFactory).create_request_repository(),
    )

    container.register_singleton(
        MachineRepository,
        lambda c: c.get(RepositoryFactory).create_machine_repository(),
    )

    def create_template_repository(container: DIContainer) -> TemplateRepository:
        """Create TemplateRepository."""
        return create_template_repository_impl(
            template_manager=container.get(TemplateConfigurationManager),
            logger=container.get(LoggingPort),
        )

    # Register with appropriate factory functions
    container.register_singleton(TemplateRepository, create_template_repository)


def _register_caching_services(container: DIContainer) -> None:
    """Register caching services."""
    from config.managers.configuration_manager import ConfigurationManager
    from domain.base import UnitOfWorkFactory
    from infrastructure.caching.request_cache_service import RequestCacheService

    def create_request_cache_service(c: DIContainer) -> RequestCacheService:
        return RequestCacheService(
            uow_factory=c.get(UnitOfWorkFactory),
            config_manager=c.get(ConfigurationManager),
            logger=c.get(LoggingPort),
        )

    container.register_singleton(RequestCacheService, create_request_cache_service)

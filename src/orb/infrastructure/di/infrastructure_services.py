"""Infrastructure service registrations for dependency injection."""

from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.machine.repository import MachineRepository
from orb.domain.request.repository import RequestRepository
from orb.domain.template.repository import TemplateRepository
from orb.infrastructure.di.container import DIContainer
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager


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
        from orb.application.services.template_defaults_service import (
            TemplateDefaultsService,
        )

        return TemplateDefaultsService(
            config_manager=c.get(ConfigurationPort),
            logger=c.get(LoggingPort),
        )

    from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort

    container.register_singleton(TemplateDefaultsPort, create_template_defaults_service)

    # Register template generation service
    def create_template_generation_service(c):
        """Create template generation service with injected dependencies."""
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.application.services.template_generation_service import (
            TemplateGenerationService,
        )
        from orb.domain.base.ports.scheduler_port import SchedulerPort
        from orb.domain.base.ports.template_example_generator_port import (
            TemplateExampleGeneratorPort,
        )

        return TemplateGenerationService(
            config_manager=c.get(ConfigurationPort),
            scheduler_strategy=c.get(SchedulerPort),
            logger=c.get(LoggingPort),
            provider_registry_service=c.get(ProviderRegistryService),
            template_example_generator=c.get(TemplateExampleGeneratorPort),
        )

    from orb.application.services.template_generation_service import TemplateGenerationService

    container.register_singleton(TemplateGenerationService, create_template_generation_service)

    # Register TemplateFactory as a singleton so handlers can receive it via DI
    def create_template_factory(c: DIContainer):
        from orb.domain.template.factory import TemplateFactory

        factory = TemplateFactory(logger=c.get(LoggingPort))
        try:
            from orb.providers.aws.registration import register_aws_template_factory

            register_aws_template_factory(factory, c.get(LoggingPort))
        except ImportError:
            logger = c.get(LoggingPort)
            logger.debug(
                "AWS provider module not available; AWS-specific templates will not be registered."
            )
        return factory

    from orb.domain.template.factory import TemplateFactory

    container.register_singleton(TemplateFactory, create_template_factory)

    # Register template configuration manager with factory function
    def create_template_configuration_manager(
        c: DIContainer,
    ) -> TemplateConfigurationManager:
        """Create TemplateConfigurationManager."""
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.config.managers.configuration_manager import ConfigurationManager
        from orb.domain.base.ports.scheduler_port import SchedulerPort
        from orb.domain.template.factory import TemplateFactory
        from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort

        return TemplateConfigurationManager(
            config_manager=c.get(ConfigurationManager),
            scheduler_strategy=c.get(SchedulerPort),
            logger=c.get(LoggingPort),
            event_publisher=None,
            template_defaults_service=c.get(TemplateDefaultsPort),  # type: ignore[arg-type]
            provider_registry_service=c.get(ProviderRegistryService),
            template_factory=c.get(TemplateFactory),
        )

    container.register_singleton(
        TemplateConfigurationManager, create_template_configuration_manager
    )

    # Check if AMI resolution is enabled via AWS extensions
    _register_ami_resolver_if_enabled(container)


def _register_ami_resolver_if_enabled(_container: DIContainer) -> None:
    """Register AMI resolver when implemented.

    TODO: CachingAMIResolver is not yet implemented. When ready, check
    TemplateExtensionRegistry for AWS AMI resolution config and register
    the resolver against TemplateResolverPort.
    """


def _register_repository_services(container: DIContainer) -> None:
    """Register repository services."""
    from orb.infrastructure.template.configuration_manager import (
        TemplateConfigurationManager,
    )
    from orb.infrastructure.template.template_repository_impl import (
        create_template_repository_impl,
    )
    from orb.infrastructure.utilities.factories.repository_factory import RepositoryFactory

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
    from orb.config.managers.configuration_manager import ConfigurationManager
    from orb.domain.base import UnitOfWorkFactory
    from orb.infrastructure.caching.request_cache_service import RequestCacheService

    def create_request_cache_service(c: DIContainer) -> RequestCacheService:
        return RequestCacheService(
            uow_factory=c.get(UnitOfWorkFactory),
            config_manager=c.get(ConfigurationManager),
            logger=c.get(LoggingPort),
        )

    container.register_singleton(RequestCacheService, create_request_cache_service)

"""Infrastructure service registrations for dependency injection."""

from domain.base.ports.logging_port import LoggingPort
from domain.base.ports.configuration_port import ConfigurationPort
from domain.machine.repository import MachineRepository
from domain.request.repository import RequestRepository
from domain.template.repository import TemplateRepository
from infrastructure.di.container import DIContainer
from infrastructure.logging.logger import get_logger
from infrastructure.template.configuration_manager import TemplateConfigurationManager



def register_infrastructure_services(container: DIContainer) -> None:
    """Register infrastructure services."""

    # Register template services
    _register_template_services(container)

    # Register repository services
    _register_repository_services(container)


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

    # Register template configuration manager with factory function
    def create_template_configuration_manager(
        container: DIContainer,
    ) -> TemplateConfigurationManager:
        """Create TemplateConfigurationManager."""
        from domain.base.ports.scheduler_port import SchedulerPort

        return TemplateConfigurationManager(
            config_manager=container.get(ConfigurationPort),
            scheduler_strategy=container.get(SchedulerPort),
            logger=container.get(LoggingPort),
            event_publisher=None,
            provider_capability_service=None,
            template_defaults_service=container.get(TemplateDefaultsPort),
        )

    container.register_singleton(
        TemplateConfigurationManager, create_template_configuration_manager
    )







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

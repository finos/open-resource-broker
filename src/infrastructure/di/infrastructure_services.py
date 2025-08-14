"""Infrastructure service registrations for dependency injection."""

from src.domain.base.ports import LoggingPort
from src.domain.machine.repository import MachineRepository
from src.domain.request.repository import RequestRepository
from src.domain.template.repository import TemplateRepository
from src.infrastructure.di.container import DIContainer
from src.infrastructure.logging.logger import get_logger
from src.infrastructure.template.configuration_manager import (
    TemplateConfigurationManager,
)
from src.providers.aws.infrastructure.template.caching_ami_resolver import (
    CachingAMIResolver,
)


def register_infrastructure_services(container: DIContainer) -> None:
    """Register infrastructure services."""

    # Register template services
    _register_template_services(container)

    # Register repository services
    _register_repository_services(container)


def _register_template_services(container: DIContainer) -> None:
    """Register template configuration services."""

    # Register template configuration manager
    container.register_singleton(TemplateConfigurationManager)

    # Register template defaults port with service implementation
    from src.application.services.template_defaults_service import (
        TemplateDefaultsService,
    )
    from src.domain.template.ports.template_defaults_port import TemplateDefaultsPort

    container.register_singleton(TemplateDefaultsPort, TemplateDefaultsService)

    # Check if AMI resolution is enabled via AWS extensions
    _register_ami_resolver_if_enabled(container)


def _register_ami_resolver_if_enabled(container: DIContainer) -> None:
    """Register AMI resolver if enabled in AWS provider extensions."""
    try:
        from src.config.manager import ConfigurationManager
        from src.domain.template.extensions import TemplateExtensionRegistry

        config_manager = container.get(ConfigurationManager)
        logger = get_logger(__name__)

        # Check if AWS extensions are registered
        if not TemplateExtensionRegistry.has_extension("aws"):
            logger.debug("AWS extensions not registered, skipping AMI resolver registration")
            return

        # Try to get AWS provider configuration
        try:
            provider_config = config_manager.get_provider_config()

            # Look for AWS provider defaults
            if (
                hasattr(provider_config, "provider_defaults")
                and "aws" in provider_config.provider_defaults
            ):
                aws_defaults = provider_config.provider_defaults["aws"]
                if hasattr(aws_defaults, "extensions"):
                    # Create AWS extension config from provider defaults using registry
                    aws_extension_config = TemplateExtensionRegistry.create_extension_config(
                        "aws", aws_defaults.extensions or {}
                    )

                    # Check if AMI resolution is enabled
                    if aws_extension_config and aws_extension_config.ami_resolution.enabled:
                        container.register_singleton(CachingAMIResolver)
                        # Register interface to resolve to concrete implementation
                        from src.domain.base.ports.template_resolver_port import (
                            TemplateResolverPort,
                        )

                        container.register_singleton(
                            TemplateResolverPort, lambda c: c.get(CachingAMIResolver)
                        )
                        logger.info(
                            "AMI resolver registered - AMI resolution enabled in AWS extensions"
                        )
                        return

            # Fallback: check if any AWS provider instances have AMI resolution enabled
            if hasattr(provider_config, "providers"):
                for provider in provider_config.providers:
                    if provider.type == "aws" and hasattr(provider, "extensions"):
                        try:
                            instance_extension_config = (
                                TemplateExtensionRegistry.create_extension_config(
                                    "aws", provider.extensions or {}
                                )
                            )
                            if (
                                instance_extension_config
                                and instance_extension_config.ami_resolution.enabled
                            ):
                                container.register_singleton(CachingAMIResolver)
                                # Register interface to resolve to concrete
                                # implementation
                                from src.domain.base.ports.template_resolver_port import (
                                    TemplateResolverPort,
                                )

                                container.register_singleton(
                                    TemplateResolverPort,
                                    lambda c: c.get(CachingAMIResolver),
                                )
                                logger.info(
                                    f"AMI resolver registered - enabled in AWS provider instance: {provider.name}"
                                )
                                return
                        except Exception as e:
                            logger.debug(
                                f"Could not parse extensions for provider { provider.name}: {e}"
                            )

            # Default: register with default AWS extension config
            default_aws_config = TemplateExtensionRegistry.create_extension_config("aws", {})
            if default_aws_config and default_aws_config.ami_resolution.enabled:
                container.register_singleton(CachingAMIResolver)
                # Register interface to resolve to concrete implementation
                from src.domain.base.ports.template_resolver_port import (
                    TemplateResolverPort,
                )

                container.register_singleton(
                    TemplateResolverPort, lambda c: c.get(CachingAMIResolver)
                )
                logger.info("AMI resolver registered with default AWS extension configuration")
            else:
                logger.debug("AMI resolution disabled in default AWS configuration")

        except Exception as e:
            logger.warning(f"Could not determine AMI resolution configuration: {e}")
            # Register with default configuration as fallback
            container.register_singleton(CachingAMIResolver)
            # Register interface to resolve to concrete implementation
            from src.domain.base.ports.template_resolver_port import (
                TemplateResolverPort,
            )

            container.register_singleton(TemplateResolverPort, lambda c: c.get(CachingAMIResolver))
            logger.info("AMI resolver registered with fallback configuration")

    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"Failed to register AMI resolver: {e}")


def _register_repository_services(container: DIContainer) -> None:
    """Register repository services."""
    from src.infrastructure.template.configuration_manager import (
        TemplateConfigurationManager,
    )
    from src.infrastructure.template.template_repository_impl import (
        create_template_repository_impl,
    )
    from src.infrastructure.utilities.factories.repository_factory import (
        RepositoryFactory,
    )

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

    def create_template_configuration_manager(
        container: DIContainer,
    ) -> TemplateConfigurationManager:
        """Create TemplateConfigurationManager."""
        from src.config.manager import ConfigurationManager
        from src.domain.base.ports.scheduler_port import SchedulerPort

        return TemplateConfigurationManager(
            config_manager=container.get(ConfigurationManager),
            scheduler_strategy=container.get(SchedulerPort),
            logger=container.get(LoggingPort),
            event_publisher=None,  # Optional
            provider_capability_service=None,  # Optional
            template_defaults_service=None,  # Optional
        )

    def create_template_repository(container: DIContainer) -> TemplateRepository:
        """Create TemplateRepository."""
        return create_template_repository_impl(
            template_manager=container.get(TemplateConfigurationManager),
            logger=container.get(LoggingPort),
        )

    # Register with proper factory functions
    container.register_singleton(
        TemplateConfigurationManager, create_template_configuration_manager
    )
    container.register_singleton(TemplateRepository, create_template_repository)

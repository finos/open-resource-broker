"""Template factory for creating provider-specific templates with extensions."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Protocol

from domain.base.ports.logging_port import LoggingPort
from domain.template.aggregate import Template
from domain.template.extensions import TemplateExtensionRegistry


class TemplateFactoryPort(Protocol):
    """Port for template factory following DIP."""

    def create_template(
        self, template_data: Dict[str, Any], provider_type: Optional[str] = None
    ) -> Template:
        """Create appropriate template type based on provider."""
        ...

    def supports_provider(self, provider_type: str) -> bool:
        """Check if factory supports a provider type."""
        ...


class BaseTemplateFactory(ABC):
    """Abstract base template factory."""

    @abstractmethod
    def create_template(
        self, template_data: Dict[str, Any], provider_type: Optional[str] = None
    ) -> Template:
        """Create appropriate template type based on provider."""

    @abstractmethod
    def supports_provider(self, provider_type: str) -> bool:
        """Check if factory supports a provider type."""


class TemplateFactory(BaseTemplateFactory):
    """Factory for creating provider-specific templates with extensions.

    This factory follows the Factory Pattern and integrates with the template
    extension registry to create appropriate template types based on provider.
    It follows SOLID principles:
    - SRP: Single responsibility of creating templates
    - OCP: Open for extension (new providers), closed for modification
    - DIP: Depends on abstractions (ports) not concretions
    """

    def __init__(
        self,
        extension_registry: Optional[TemplateExtensionRegistry] = None,
        logger: Optional[LoggingPort] = None,
    ):
        """Initialize template factory.

        Args:
            extension_registry: Registry for provider extensions (defaults to global registry)
            logger: Logger port for logging factory operations
        """
        self._extension_registry = extension_registry or TemplateExtensionRegistry
        self._logger = logger

        # Registry of provider-specific template classes
        self._provider_template_classes: Dict[str, type] = {}

        # Register built-in provider template classes
        self._register_builtin_providers()

    def _register_builtin_providers(self) -> None:
        """Register built-in provider template classes."""
        try:
            # Register AWS template class
            from providers.aws.domain.template.aggregate import AWSTemplate

            self._provider_template_classes["aws"] = AWSTemplate

            if self._logger:
                self._logger.debug("Registered AWS template class")
        except ImportError:
            if self._logger:
                self._logger.warning("AWS template class not available for registration")

        # Future providers can be registered here or via register_provider_template_class
        # noqa:COMMENTED section-start
        # try:
        #     from providers.provider1.domain.template.aggregate import Provider1Template
        #     self._provider_template_classes['provider1'] = Provider1Template
        # except ImportError:
        #     pass
        # noqa:COMMENTED section-end

    def register_provider_template_class(self, provider_type: str, template_class: type) -> None:
        """Register a provider-specific template class.

        Args:
            provider_type: The provider type (e.g., 'aws', 'provider1', 'provider2')
            template_class: The template class for this provider
        """
        if not issubclass(template_class, Template):
            raise ValueError(f"Template class must inherit from Template, got {template_class}")

        self._provider_template_classes[provider_type] = template_class

        if self._logger:
            self._logger.debug(f"Registered template class for provider: {provider_type}")

    def create_template(
        self, template_data: Dict[str, Any], provider_type: Optional[str] = None
    ) -> Template:
        """Create appropriate template type based on provider.

        Args:
            template_data: Template configuration data
            provider_type: Provider type to create template for

        Returns:
            Template instance (provider-specific if available, otherwise core Template)
        """
        # Determine provider type if not provided
        if not provider_type:
            provider_type = self._determine_provider_type(template_data)

        # Log template creation
        if self._logger:
            self._logger.debug(f"Creating template for provider: {provider_type}")

        # Create provider-specific template if available
        if provider_type and provider_type in self._provider_template_classes:
            try:
                template_class = self._provider_template_classes[provider_type]
                template = template_class(**template_data)

                if self._logger:
                    self._logger.debug(f"Created {provider_type} template: {template.template_id}")

                return template
            except Exception as e:
                if self._logger:
                    self._logger.error(f"Failed to create {provider_type} template: {e}")
                # Fall back to core template

        # Fall back to core template
        try:
            template = Template(**template_data)

            if self._logger:
                self._logger.debug(f"Created core template: {template.template_id}")

            return template
        except Exception as e:
            if self._logger:
                self._logger.error(f"Failed to create core template: {e}")
            raise

    def supports_provider(self, provider_type: str) -> bool:
        """Check if factory supports a provider type.

        Args:
            provider_type: The provider type to check

        Returns:
            True if the provider is supported (has registered template class)
        """
        return provider_type in self._provider_template_classes

    def get_supported_providers(self) -> list[str]:
        """Get list of supported provider types.

        Returns:
            List of provider types that have registered template classes
        """
        return list(self._provider_template_classes.keys())

    def _determine_provider_type(self, template_data: Dict[str, Any]) -> Optional[str]:
        """Determine provider type from template data.

        Args:
            template_data: Template configuration data

        Returns:
            Provider type if determinable, None otherwise
        """
        # Check explicit provider_type field
        if "provider_type" in template_data:
            return template_data["provider_type"]

        # Check provider_name field and extract type
        if "provider_name" in template_data:
            provider_name = template_data["provider_name"]
            if isinstance(provider_name, str) and "-" in provider_name:
                return provider_name.split("-")[0]

        # Check for provider-specific fields to infer type
        aws_specific_fields = {
            "provider_api",
            "fleet_type",
            "fleet_role",
            "spot_fleet_request_expiry",
            "allocation_strategy",
            "volume_type",
            "iops",
            "ami_resolution",
        }

        if any(field in template_data for field in aws_specific_fields):
            return "aws"

        # Could add similar logic for other providers
        # noqa:COMMENTED section-start
        # provider1_specific_fields = {'vm_size', 'os_disk_type', ...}
        # if any(field in template_data for field in provider1_specific_fields):
        #     return 'provider1'
        # noqa:COMMENTED section-end

        return None

    def create_template_with_extensions(
        self,
        template_data: Dict[str, Any],
        provider_type: Optional[str] = None,
        extension_data: Optional[Dict[str, Any]] = None,
    ) -> Template:
        """Create template with provider extensions applied.

        Args:
            template_data: Core template configuration data
            provider_type: Provider type to create template for
            extension_data: Additional extension configuration data

        Returns:
            Template instance with extensions applied
        """
        # Merge extension data if provided
        if extension_data:
            merged_data = {**template_data, **extension_data}
        else:
            merged_data = template_data.copy()

        # Apply extension defaults from registry
        if provider_type and self._extension_registry.has_extension(provider_type):
            extension_defaults = self._extension_registry.get_extension_defaults(
                provider_type, extension_data
            )
            # Extension defaults have lower priority than explicit template data
            merged_data = {**extension_defaults, **merged_data}

        return self.create_template(merged_data, provider_type)


# Factory instance for dependency injection
def create_template_factory(
    extension_registry: Optional[TemplateExtensionRegistry] = None,
    logger: Optional[LoggingPort] = None,
) -> TemplateFactory:
    """Create a template factory instance.

    Args:
        extension_registry: Optional extension registry
        logger: Optional logger port

    Returns:
        Configured template factory instance
    """
    return TemplateFactory(extension_registry, logger)


# Default factory instance
_default_factory: Optional[TemplateFactory] = None


def get_default_template_factory() -> TemplateFactory:
    """Get the default template factory instance.

    Returns:
        Default template factory instance
    """
    global _default_factory
    if _default_factory is None:
        _default_factory = TemplateFactory()
    return _default_factory


def set_default_template_factory(factory: TemplateFactory) -> None:
    """Set the default template factory instance.

    Args:
        factory: Template factory to set as default
    """
    global _default_factory
    _default_factory = factory

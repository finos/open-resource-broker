"""Azure provider onboarding through the canonical plugin contract."""

from __future__ import annotations

from typing import Any, Optional

from orb.providers.base.provider_plugin import ProviderPlugin


class AzurePlugin(ProviderPlugin):
    """Expose Azure's registration components to shared provider bootstrap."""

    provider_name = "azure"

    def strategy_factory(self) -> Any:
        """Return the default-instance Azure strategy factory."""
        from orb.providers.azure.registration import create_azure_strategy

        def create_default_azure_strategy(provider_config: Any) -> Any:
            """Create the strategy registered for the unnamed Azure provider type."""
            return create_azure_strategy(
                provider_config,
                provider_instance_name="azure-default",
            )

        return create_default_azure_strategy

    def config_factory(self) -> Any:
        """Return Azure's typed configuration factory."""
        from orb.providers.azure.registration import create_azure_config

        return create_azure_config

    def validator_factory(self) -> Optional[Any]:
        """Return Azure's template validator factory."""
        from orb.providers.azure.registration import create_azure_validator

        return create_azure_validator

    def strategy_class(self) -> Optional[type]:
        """Return the concrete Azure strategy class."""
        from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy

        return AzureProviderStrategy

    def provider_settings_class(self) -> Optional[type]:
        """Return the Azure settings model used by configuration loading."""
        from orb.providers.azure.configuration.config import AzureProviderConfig

        return AzureProviderConfig

    def template_dto_config(self) -> Any:
        """Return the Azure template extension model."""
        from orb.providers.azure.configuration.template_extension import (
            AzureTemplateExtensionConfig,
        )

        return AzureTemplateExtensionConfig

    def template_class(self) -> Optional[type]:
        """Return the Azure template aggregate class."""
        from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate

        return AzureTemplate

    def cli_spec(self) -> Any:
        """Return Azure's CLI argument specification."""
        from orb.providers.azure.cli.azure_cli_spec import AzureCLISpec

        return AzureCLISpec()

    def field_mapping(self) -> Any:
        """Return Azure's HostFactory field mapping."""
        from orb.providers.azure.scheduler.hostfactory_field_mapping import AzureFieldMapping

        return AzureFieldMapping()

    def defaults_loader(self) -> Any:
        """Return Azure's provider-owned defaults loader."""
        from orb.providers.azure.defaults_loader import AzureDefaultsLoader

        return AzureDefaultsLoader()

    def template_example_generator(self, container: Any) -> Any:
        """Return Azure's template example generator."""
        from orb.providers.azure.infrastructure.adapters.template_example_generator_adapter import (
            AzureTemplateExampleGeneratorAdapter,
        )

        return AzureTemplateExampleGeneratorAdapter()

    def register_services_with_di(self, container: Any) -> None:
        """Delegate Azure's established DI registrations without duplicating them."""
        from orb.providers.azure.registration import register_azure_services_with_di

        register_azure_services_with_di(container)


__all__ = ["AzurePlugin"]

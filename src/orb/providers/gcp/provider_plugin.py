"""GCP provider onboarding through the provider plugin contract."""

from __future__ import annotations

from typing import Any

from orb.providers.base.provider_plugin import ProviderPlugin


class GCPPlugin(ProviderPlugin):
    """Register GCP's strategy, configuration, and template satellites."""

    provider_name = "gcp"

    def strategy_factory(self) -> Any:
        """Return the GCP strategy factory."""
        from orb.providers.gcp.factories import create_gcp_strategy

        return create_gcp_strategy

    def config_factory(self) -> Any:
        """Return the GCP configuration factory."""
        from orb.providers.gcp.factories import create_gcp_config

        return create_gcp_config

    def validator_factory(self) -> Any:
        """Return the GCP template validator factory."""
        from orb.providers.gcp.factories import create_gcp_validator

        return create_gcp_validator

    def strategy_class(self) -> type:
        """Return the concrete GCP strategy type."""
        from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy

        return GCPProviderStrategy

    def provider_settings_class(self) -> type:
        """Return the GCP provider settings type."""
        from orb.providers.gcp.configuration.config import GCPProviderConfig

        return GCPProviderConfig

    def template_dto_config(self) -> type:
        """Return the GCP template extension type."""
        from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig

        return GCPTemplateExtensionConfig

    def template_class(self) -> type:
        """Return the GCP template aggregate type."""
        from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate

        return GCPTemplate

    def cli_spec(self) -> Any:
        """Return the GCP CLI argument specification."""
        from orb.providers.gcp.cli.gcp_cli_spec import GCPCLISpec

        return GCPCLISpec()

    def field_mapping(self) -> Any:
        """Return GCP's HostFactory field mapping."""
        from orb.providers.gcp.scheduler.hostfactory_field_mapping import GCPFieldMapping

        return GCPFieldMapping()

    def defaults_loader(self) -> Any:
        """Return the GCP provider defaults loader."""
        from orb.providers.gcp.defaults_loader import GCPDefaultsLoader

        return GCPDefaultsLoader()

    def template_example_generator(self, container: Any) -> Any:
        """Return the GCP template example generator."""
        from orb.providers.gcp.adapters.template_example_generator_adapter import (
            GCPTemplateExampleGeneratorAdapter,
        )

        return GCPTemplateExampleGeneratorAdapter()

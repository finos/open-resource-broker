"""OCI integration through ORB's provider plugin contract."""

from typing import Any

from orb.providers.base.provider_plugin import ProviderPlugin


class OCIPlugin(ProviderPlugin):
    """Register OCI's provider-owned configuration and adapters."""

    provider_name = "oci"

    def display_name(self) -> str:
        return "Oracle Cloud Infrastructure (OCI)"

    def strategy_factory(self) -> Any:
        from orb.providers.oci.registration import create_oci_strategy

        return create_oci_strategy

    def config_factory(self) -> Any:
        from orb.providers.oci.registration import create_oci_config

        return create_oci_config

    def strategy_class(self) -> type:
        from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy

        return OCIProviderStrategy

    def default_api(self) -> str:
        return "OCICompute"

    def provider_settings_class(self) -> type:
        from orb.providers.oci.configuration.config import OCIProviderConfig

        return OCIProviderConfig

    def template_dto_config(self) -> type:
        from orb.providers.oci.configuration.template_extension import OCITemplateExtensionConfig

        return OCITemplateExtensionConfig

    def template_class(self) -> type:
        from orb.providers.oci.domain.template import OCITemplate

        return OCITemplate

    def cli_spec(self) -> Any:
        from orb.providers.oci.cli.oci_cli_spec import OCICLISpec

        return OCICLISpec()

    def field_mapping(self) -> Any:
        from orb.providers.oci.scheduler.hostfactory_field_mapping import OCIFieldMapping

        return OCIFieldMapping()

    def defaults_loader(self) -> Any:
        from orb.providers.oci.defaults_loader import OCIDefaultsLoader

        return OCIDefaultsLoader()

    def template_example_generator(self, container: Any) -> Any:
        from orb.providers.oci.adapters.template_example_generator_adapter import (
            OCITemplateExampleGeneratorAdapter,
        )

        return OCITemplateExampleGeneratorAdapter()

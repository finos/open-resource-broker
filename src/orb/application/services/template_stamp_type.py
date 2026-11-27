"""Which provider type a template row may be stamped with during default resolution."""

from typing import Any, Optional

from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
from orb.domain.base.utils import extract_provider_type


class StampTypeResolver:
    """Decides the provider type to write onto a template row that lacks one."""

    def __init__(
        self,
        config_manager: ConfigurationPort,
        logger: LoggingPort,
        provider_registry: Optional[ProviderRegistryPort] = None,
    ) -> None:
        self._config_manager = config_manager
        self._logger = logger
        self._provider_registry = provider_registry

    def trusted_stamp_type(
        self, template_dict: dict[str, Any], selected_instance: Optional[str]
    ) -> Optional[str]:
        """Return the type to stamp, or None when stamping would lose the row.

        The load filter drops rows whose type is not active. The selected
        instance (the active one, or the override) counts as active; a row
        pointing at another inactive instance is left as it is today.
        """
        candidate = self._candidate_stamp_type(template_dict, selected_instance)
        if not candidate:
            return None
        try:
            provider_config = self._config_manager.get_provider_config()
            admitted = {p.type for p in provider_config.get_active_providers()}  # type: ignore[union-attr]
        except Exception as e:
            self._logger.debug("Could not read active provider types: %s", e)
            return candidate
        if not admitted:
            return candidate  # the filter keeps every row when no active type is known
        if selected_instance:
            selected_type = self._configured_provider_type(selected_instance)
            if selected_type:
                admitted.add(selected_type)
        return candidate if candidate in admitted else None

    def _candidate_stamp_type(
        self, template_dict: dict[str, Any], selected_instance: Optional[str]
    ) -> Optional[str]:
        """Return the provider type that may be written onto a row, or None.

        Trusted: the row's own declared type, the type of an instance found in
        ``config.providers``, or a name-prefix type the provider registry
        accepts. A name-prefix guess for an unknown instance ("default",
        "My_Cluster") is never stamped.
        """
        declared = template_dict.get("provider_type") or template_dict.get("providerType")
        if declared:
            return declared
        instance = (
            template_dict.get("provider_name") or template_dict.get("providerName")
        ) or selected_instance
        if not instance:
            return None
        configured = self._configured_provider_type(instance)
        if configured:
            return configured
        guess = extract_provider_type(instance)
        if guess and self._provider_registry is not None:
            try:
                if self._provider_registry.is_provider_registered(guess):
                    return guess
            except Exception as e:
                self._logger.warning("Could not check provider type %s: %s", guess, e)
        return None

    def _configured_provider_type(self, provider_instance_name: str) -> Optional[str]:
        """Type of an instance listed in config.providers, else None (no name guessing)."""
        try:
            provider_config = self._config_manager.get_provider_config()
            for provider in getattr(provider_config, "providers", None) or []:
                if provider.name == provider_instance_name:
                    return provider.type
        except Exception as e:
            self._logger.warning(
                "Could not determine provider type for %s: %s", provider_instance_name, e
            )
        return None

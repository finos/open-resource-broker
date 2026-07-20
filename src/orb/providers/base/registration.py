"""Declarative provider registration.

:class:`ProviderRegistrationSpec` bundles the satellite pieces a provider
contributes, and :func:`register_provider_complete` runs the eight guarded
registration steps in the canonical order used by
:meth:`~orb.providers.base.provider_plugin.ProviderPlugin.initialize_provider`:

    1. provider settings
    2. template DTO extension
    3. auth strategies
    4. template class
    5. CLI spec
    6. HostFactory field mapping
    7. defaults loader
    8. storage / extra initialization

Each step is individually guarded so an optional dependency being absent
(``ImportError``) is skipped silently rather than aborting the whole
registration.  Idempotency is enforced against the shared
:data:`~orb.providers.base.provider_plugin._initialized_providers` guard set,
so a second registration for the same provider name is a safe no-op, and a
failed registration does NOT poison the guard (a fixed retry re-runs fully).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from orb.providers.base.provider_plugin import _initialized_providers

_logger = logging.getLogger(__name__)


@dataclass
class ProviderRegistrationSpec:
    """Declarative bundle of a provider's registration satellites.

    Every field is optional; a ``None`` value means "skip this step".  The
    ``*_class`` / ``*_instance`` distinction mirrors the underlying registries:
    settings/template classes are registered as classes, while CLI spec, field
    mapping and defaults loader are registered as instances.
    """

    provider_name: str
    settings_class: Optional[type] = None
    dto_config_class: Optional[type] = None
    register_auth: Optional[Callable[[Optional[Any]], None]] = None
    template_class: Optional[type] = None
    template_factory: Optional[Any] = None
    cli_spec_instance: Optional[Any] = None
    field_mapping_instance: Optional[Any] = None
    defaults_loader_instance: Optional[Any] = None
    extra_init: Optional[Callable[[Optional[Any]], None]] = None


def register_provider_complete(
    spec: ProviderRegistrationSpec,
    logger: Optional[Any] = None,
) -> None:
    """Run the eight guarded registration steps for *spec*.

    Idempotent against :data:`_initialized_providers`.  On failure the provider
    name is NOT recorded, so a retry after fixing the root cause re-runs the
    full sequence.
    """
    provider_name = spec.provider_name

    if provider_name in _initialized_providers:
        _logger.debug(
            "Provider %r already initialized — skipping",
            provider_name,
        )
        return

    try:
        # 1. Provider settings
        if spec.settings_class is not None:
            try:
                from orb.config.schemas.provider_settings_registry import (
                    ProviderSettingsRegistry,
                )

                if ProviderSettingsRegistry.get_or_none(provider_name) is None:
                    ProviderSettingsRegistry.register_provider_settings(
                        provider_name, spec.settings_class
                    )
            except ImportError as _exc:
                _logger.debug(
                    "Skipping %r settings registration — optional dependency absent: %s",
                    provider_name,
                    _exc,
                )

        # 2. Template DTO extension
        if spec.dto_config_class is not None:
            try:
                from orb.infrastructure.registry.template_extension_registry import (
                    TemplateExtensionRegistry,
                )

                if not TemplateExtensionRegistry.has_extension(provider_name):
                    TemplateExtensionRegistry.register_extension(
                        provider_name, spec.dto_config_class
                    )
            except ImportError as _exc:
                _logger.debug(
                    "Skipping %r DTO-extension registration — optional dependency absent: %s",
                    provider_name,
                    _exc,
                )

        # 3. Auth strategies (optional hook)
        if spec.register_auth is not None:
            spec.register_auth(logger)

        # 4. Template class (optional)
        if spec.template_class is not None and spec.template_factory is not None:
            try:
                spec.template_factory.register_provider_template_class(
                    provider_name, spec.template_class
                )
            except Exception as exc:
                _logger.debug(
                    "Could not register template class for %r: %s",
                    provider_name,
                    exc,
                )

        # 5. CLI spec
        if spec.cli_spec_instance is not None:
            try:
                from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry

                CLISpecRegistry.register(provider_name, spec.cli_spec_instance)
            except ImportError as _exc:
                _logger.debug(
                    "Skipping %r CLI-spec registration — optional dependency absent: %s",
                    provider_name,
                    _exc,
                )

        # 6. HostFactory field mapping
        if spec.field_mapping_instance is not None:
            try:
                from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import (
                    FieldMappingRegistry,
                )

                FieldMappingRegistry.register(provider_name, spec.field_mapping_instance)
            except ImportError as _exc:
                _logger.debug(
                    "Skipping %r field-mapping registration — optional dependency absent: %s",
                    provider_name,
                    _exc,
                )

        # 7. Defaults loader
        if spec.defaults_loader_instance is not None:
            try:
                from orb.providers.registry.defaults_loader_registry import (
                    DefaultsLoaderRegistry,
                )

                DefaultsLoaderRegistry.register(provider_name, spec.defaults_loader_instance)
            except ImportError as _exc:
                _logger.debug(
                    "Skipping %r defaults-loader registration — optional dependency absent: %s",
                    provider_name,
                    _exc,
                )

        # 8. Storage / provider-specific extra initialization (optional hook)
        if spec.extra_init is not None:
            spec.extra_init(logger)

        _initialized_providers.add(provider_name)

        if logger:
            logger.info(
                "%s provider initialization completed successfully",
                provider_name,
            )

    except Exception as exc:
        # Deliberately NOT adding to _initialized_providers so a retry
        # after fixing the root cause will re-attempt fully.
        error_msg = f"{provider_name} provider initialization failed: {exc}"
        if logger:
            logger.error(error_msg, exc_info=True)
        raise


__all__ = ["ProviderRegistrationSpec", "register_provider_complete"]

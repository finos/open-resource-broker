"""Unit tests for GCP provider registration isolation and error handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.utilities.provider_import_isolation import assert_no_auto_registration_on_import


@pytest.mark.unit
def test_gcp_auto_registration_not_fired_at_import() -> None:
    """Importing orb.providers.gcp.registration must not auto-register any provider satellites."""
    assert_no_auto_registration_on_import("orb.providers.gcp.registration", "gcp")


@pytest.mark.unit
def test_bootstrap_provider_utility_services_logs_gcp_init_failure_traceback() -> None:
    """Bootstrap _register_provider_utility_services must log exc_info=True when initialize_gcp_provider fails."""
    from orb.bootstrap.provider_services import _register_provider_utility_services

    container = MagicMock()
    logger = MagicMock()
    mock_registry = MagicMock()
    mock_registry.get_registered_types.return_value = ["gcp"]

    with (
        patch("orb.bootstrap.provider_services.get_logger", return_value=logger),
        patch("orb.providers.registry.get_provider_registry", return_value=mock_registry),
        patch(
            "orb.providers.gcp.registration.initialize_gcp_provider",
            side_effect=RuntimeError("gcp init boom"),
        ),
    ):
        _register_provider_utility_services(container)

    logger.warning.assert_called_once_with(
        "Failed to register %s utility services: %s",
        "gcp",
        "gcp init boom",
        exc_info=True,
    )


@pytest.mark.unit
def test_register_gcp_provider_settings_logs_import_error() -> None:
    """register_gcp_provider_settings must log ImportError rather than silently passing."""
    from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
    from orb.providers.gcp.registration import register_gcp_provider_settings

    logger = MagicMock()
    with patch.object(
        ProviderSettingsRegistry,
        "get_or_none",
        side_effect=ImportError("optional gcp settings dependency missing"),
    ):
        register_gcp_provider_settings(logger=logger)

    logger.debug.assert_called_once()


@pytest.mark.unit
def test_register_gcp_provider_settings_logs_and_chains_unexpected_exception() -> None:
    """register_gcp_provider_settings must log exc_info=True and preserve __cause__."""
    from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
    from orb.providers.gcp.registration import register_gcp_provider_settings

    logger = MagicMock()
    cause = ValueError("corrupt gcp settings schema")
    with (
        patch.object(ProviderSettingsRegistry, "get_or_none", return_value=None),
        patch.object(
            ProviderSettingsRegistry,
            "register_provider_settings",
            side_effect=cause,
        ),
    ):
        with pytest.raises(RuntimeError, match="Failed to register GCP provider settings") as ei:
            register_gcp_provider_settings(logger=logger)

    assert ei.value.__cause__ is cause
    logger.error.assert_called_once()
    assert logger.error.call_args.kwargs.get("exc_info") is True


@pytest.mark.unit
def test_register_gcp_extensions_populates_cli_spec_when_template_extension_already_present() -> (
    None
):
    """register_gcp_extensions must still populate CLISpecRegistry if TemplateExtensionRegistry already has gcp."""
    from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
    from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
    from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig
    from orb.providers.gcp.registration import register_gcp_extensions

    ext_snapshot = dict(TemplateExtensionRegistry._extensions)
    cli_snapshot = CLISpecRegistry.all()
    try:
        TemplateExtensionRegistry.register_extension("gcp", GCPTemplateExtensionConfig)
        CLISpecRegistry.clear()
        assert CLISpecRegistry.get_or_none("gcp") is None

        register_gcp_extensions()

        assert CLISpecRegistry.get_or_none("gcp") is not None
    finally:
        TemplateExtensionRegistry._extensions.clear()
        TemplateExtensionRegistry._extensions.update(ext_snapshot)
        CLISpecRegistry.clear()
        for key, spec in cli_snapshot.items():
            CLISpecRegistry.register(key, spec)

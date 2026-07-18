"""Unit tests for ProviderConfigValidator."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.config.schemas.provider_strategy_schema import ProviderMode
from orb.providers.config_validator import ProviderConfigValidator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_validator(
    mode: ProviderMode = ProviderMode.SINGLE,
    active_providers: list | None = None,
    provider_config_exception: Exception | None = None,
    build_config_side_effect=None,
    get_or_create_side_effect=None,
) -> ProviderConfigValidator:
    """Return a ProviderConfigValidator with mocked dependencies."""

    config_manager = MagicMock()
    if provider_config_exception:
        config_manager.get_provider_config.side_effect = provider_config_exception
    else:
        provider_config = MagicMock()
        provider_config.get_mode.return_value = mode
        provider_config.get_active_providers.return_value = active_providers or []
        config_manager.get_provider_config.return_value = provider_config

    config_builder = MagicMock()
    if build_config_side_effect:
        config_builder.build_config.side_effect = build_config_side_effect

    logger = MagicMock()

    registry = MagicMock()
    if get_or_create_side_effect:
        registry.get_or_create_strategy.side_effect = get_or_create_side_effect

    return ProviderConfigValidator(config_manager, config_builder, logger, registry)


def _make_provider(name: str = "aws1") -> MagicMock:
    p = MagicMock()
    p.name = name
    return p


# ---------------------------------------------------------------------------
# validate_configuration — provider_config not found
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_config_returns_error_when_no_provider_config() -> None:
    """Returns invalid result with error when provider config is None."""
    config_manager = MagicMock()
    config_manager.get_provider_config.return_value = None
    validator = ProviderConfigValidator(config_manager, MagicMock(), MagicMock(), MagicMock())
    result = validator.validate_configuration()
    assert result["valid"] is False
    assert any("not found" in e for e in result["errors"])


@pytest.mark.unit
def test_validate_config_returns_error_on_exception() -> None:
    """Returns invalid result with error when an exception is raised."""
    validator = _make_validator(provider_config_exception=RuntimeError("config broken"))
    result = validator.validate_configuration()
    assert result["valid"] is False
    assert len(result["errors"]) > 0


# ---------------------------------------------------------------------------
# _validate_mode — NONE mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_config_none_mode_adds_error() -> None:
    """NONE mode adds 'No valid provider configuration found' error."""
    validator = _make_validator(mode=ProviderMode.NONE, active_providers=[])
    result = validator.validate_configuration()
    assert result["valid"] is False
    assert any("No valid provider" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# _validate_mode — SINGLE mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_config_single_mode_no_providers_adds_error() -> None:
    """SINGLE mode with no active providers adds an error."""
    validator = _make_validator(mode=ProviderMode.SINGLE, active_providers=[])
    result = validator.validate_configuration()
    assert result["valid"] is False
    assert any("Single provider" in e for e in result["errors"])


@pytest.mark.unit
def test_validate_config_single_mode_one_provider_valid() -> None:
    """SINGLE mode with exactly one active provider is valid."""
    provider = _make_provider("aws1")
    validator = _make_validator(mode=ProviderMode.SINGLE, active_providers=[provider])
    result = validator.validate_configuration()
    assert result["valid"] is True
    assert result["errors"] == []


@pytest.mark.unit
def test_validate_config_single_mode_multiple_providers_adds_warning() -> None:
    """SINGLE mode with multiple active providers adds a warning (but remains valid)."""
    providers = [_make_provider("aws1"), _make_provider("aws2")]
    validator = _make_validator(mode=ProviderMode.SINGLE, active_providers=providers)
    result = validator.validate_configuration()
    assert result["valid"] is True
    assert any("Multiple active providers" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# _validate_mode — MULTI mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_config_multi_mode_one_provider_adds_error() -> None:
    """MULTI mode with fewer than 2 active providers adds an error."""
    validator = _make_validator(mode=ProviderMode.MULTI, active_providers=[_make_provider()])
    result = validator.validate_configuration()
    assert result["valid"] is False
    assert any("Multi-provider" in e for e in result["errors"])


@pytest.mark.unit
def test_validate_config_multi_mode_two_providers_valid() -> None:
    """MULTI mode with exactly 2 active providers is valid."""
    providers = [_make_provider("aws1"), _make_provider("aws2")]
    validator = _make_validator(mode=ProviderMode.MULTI, active_providers=providers)
    result = validator.validate_configuration()
    assert result["valid"] is True


# ---------------------------------------------------------------------------
# _validate_providers — individual provider failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_config_provider_validation_failure_adds_error() -> None:
    """When build_config raises, the provider name appears in errors."""
    provider = _make_provider("failing_provider")
    validator = _make_validator(
        mode=ProviderMode.SINGLE,
        active_providers=[provider],
        build_config_side_effect=ValueError("bad config"),
    )
    result = validator.validate_configuration()
    assert result["valid"] is False
    assert any("failing_provider" in e for e in result["errors"])


@pytest.mark.unit
def test_validate_config_get_or_create_failure_adds_error() -> None:
    """When get_or_create_strategy raises, error is captured."""
    provider = _make_provider("prov_x")
    validator = _make_validator(
        mode=ProviderMode.SINGLE,
        active_providers=[provider],
        get_or_create_side_effect=Exception("strategy error"),
    )
    result = validator.validate_configuration()
    assert result["valid"] is False
    assert any("prov_x" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_config_returns_expected_keys() -> None:
    """validate_configuration always returns the expected keys."""
    validator = _make_validator(mode=ProviderMode.NONE, active_providers=[])
    result = validator.validate_configuration()
    for key in ("valid", "errors", "warnings", "provider_count", "mode"):
        assert key in result


@pytest.mark.unit
def test_validate_config_provider_count_matches_active_providers() -> None:
    """provider_count reflects the number of active providers."""
    providers = [_make_provider("p1"), _make_provider("p2")]
    validator = _make_validator(mode=ProviderMode.MULTI, active_providers=providers)
    result = validator.validate_configuration()
    assert result["provider_count"] == 2


@pytest.mark.unit
def test_validate_config_mode_field_contains_mode_value() -> None:
    """mode field contains the ProviderMode string value."""
    validator = _make_validator(mode=ProviderMode.SINGLE, active_providers=[_make_provider()])
    result = validator.validate_configuration()
    assert result["mode"] == ProviderMode.SINGLE.value

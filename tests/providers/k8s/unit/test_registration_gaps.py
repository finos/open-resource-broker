"""Gap-filling unit tests for k8s registration.py.

Targets uncovered lines in registration.py:
- create_k8s_config: exception paths (209-212)
- create_k8s_strategy: empty config raises RuntimeError (182, and dict paths)
- register_k8s_provider_settings: non-ImportError exception path (259-260)
- register_k8s_extensions: logger-less and exception paths (283-288)
- register_k8s_auth_strategies: inbound_auth_enabled=True path (351-357, 382-384)
- register_k8s_provider_instance: already-registered path, logger, exception path
  (440, 442-445, 465-492)
- initialize_k8s_provider: already-initialized guard, logger, exception path (556, 605, 607-613)
- is_k8s_provider_registered: registry exception path (706-709)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.k8s.registration import (
    _k8s_config_is_empty,
    create_k8s_config,
    initialize_k8s_provider,
    is_k8s_provider_registered,
    register_k8s_auth_strategies,
    register_k8s_extensions,
    register_k8s_provider,
    register_k8s_provider_instance,
    register_k8s_provider_settings,
)

# ---------------------------------------------------------------------------
# _k8s_config_is_empty — pure logic
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_k8s_config_is_empty_none() -> None:
    assert _k8s_config_is_empty(None) is True


@pytest.mark.unit
def test_k8s_config_is_empty_empty_dict() -> None:
    assert _k8s_config_is_empty({}) is True


@pytest.mark.unit
def test_k8s_config_is_empty_in_cluster_true() -> None:
    """in_cluster=True is explicit targeting — not empty."""
    assert _k8s_config_is_empty({"in_cluster": True}) is False


@pytest.mark.unit
def test_k8s_config_is_empty_in_cluster_false() -> None:
    """in_cluster=False provides no useful targeting — treated as empty."""
    assert _k8s_config_is_empty({"in_cluster": False}) is True


@pytest.mark.unit
def test_k8s_config_is_empty_kubeconfig_path() -> None:
    assert _k8s_config_is_empty({"kubeconfig_path": "/etc/kube.cfg"}) is False


@pytest.mark.unit
def test_k8s_config_is_empty_context() -> None:
    assert _k8s_config_is_empty({"context": "prod-ctx"}) is False


@pytest.mark.unit
def test_k8s_config_is_empty_non_dict_object() -> None:
    """Non-dict, non-None objects are treated as not-empty (false = has config)."""
    assert _k8s_config_is_empty(object()) is False


# ---------------------------------------------------------------------------
# create_k8s_config — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_create_k8s_config_raises_runtime_error_on_bad_data() -> None:
    """create_k8s_config wraps unexpected exceptions in RuntimeError."""
    with pytest.raises(RuntimeError, match="Failed to create Kubernetes config"):
        create_k8s_config({"invalid_field_xyz": True, "another_bad_field": object()})


# ---------------------------------------------------------------------------
# register_k8s_provider_settings — non-ImportError exception
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_register_k8s_provider_settings_non_import_error_raises() -> None:
    """register_k8s_provider_settings re-raises non-ImportError exceptions."""
    with patch(
        "orb.providers.k8s.registration.ProviderSettingsRegistry",
        side_effect=ValueError("bad registry"),
        create=True,
    ):
        with patch(
            "orb.config.schemas.provider_settings_registry.ProviderSettingsRegistry"
            ".register_provider_settings",
            side_effect=ValueError("bad registry"),
        ):
            with pytest.raises((ValueError, RuntimeError)):
                register_k8s_provider_settings()


# ---------------------------------------------------------------------------
# register_k8s_extensions — with and without logger
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_register_k8s_extensions_no_logger_does_not_crash() -> None:
    """register_k8s_extensions with no logger completes without error."""
    # Idempotent — may or may not already be registered
    register_k8s_extensions(logger=None)  # must not raise


@pytest.mark.unit
def test_register_k8s_extensions_with_logger_logs_debug() -> None:
    """register_k8s_extensions with a logger calls logger.debug on success."""
    mock_logger = MagicMock()
    register_k8s_extensions(logger=mock_logger)

    mock_logger.debug.assert_called()


@pytest.mark.unit
def test_register_k8s_extensions_exception_with_logger_logs_error() -> None:
    """register_k8s_extensions logs logger.error and re-raises on failure."""
    mock_logger = MagicMock()

    with patch(
        "orb.infrastructure.registry.template_extension_registry.TemplateExtensionRegistry"
        ".register_extension",
        side_effect=RuntimeError("registry-fail"),
    ):
        with pytest.raises(RuntimeError, match="registry-fail"):
            register_k8s_extensions(logger=mock_logger)

    mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# register_k8s_auth_strategies — inbound_auth_enabled=True path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_register_k8s_auth_strategies_disabled_with_logger_logs_debug() -> None:
    """register_k8s_auth_strategies with inbound_auth_enabled=False logs debug."""
    mock_logger = MagicMock()
    register_k8s_auth_strategies(logger=mock_logger, inbound_auth_enabled=False)
    mock_logger.debug.assert_called()


@pytest.mark.unit
def test_register_k8s_auth_strategies_enabled_registers_strategy() -> None:
    """register_k8s_auth_strategies with inbound_auth_enabled=True registers KubeAuthStrategy."""
    from orb.providers.k8s.auth.kube_auth_strategy import KubeAuthStrategy

    mock_registry = MagicMock()
    mock_registry.is_registered.return_value = False
    mock_logger = MagicMock()

    with patch(
        "orb.infrastructure.auth.registry.get_auth_registry",
        return_value=mock_registry,
    ):
        register_k8s_auth_strategies(logger=mock_logger, inbound_auth_enabled=True)

    mock_registry.register_strategy.assert_called_once_with("kubernetes", KubeAuthStrategy)


@pytest.mark.unit
def test_register_k8s_auth_strategies_import_error_logs_warning() -> None:
    """register_k8s_auth_strategies logs warning when auth registry raises ImportError."""
    mock_logger = MagicMock()

    mock_registry = MagicMock()
    mock_registry.is_registered.return_value = False
    mock_registry.register_strategy.side_effect = ImportError("kube-auth-missing")

    with patch("orb.infrastructure.auth.registry.get_auth_registry", return_value=mock_registry):
        register_k8s_auth_strategies(logger=mock_logger, inbound_auth_enabled=True)

    mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# register_k8s_provider_instance — branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_register_k8s_provider_instance_logs_success_with_logger() -> None:
    """register_k8s_provider_instance logs debug on success."""
    mock_logger = MagicMock()
    provider_instance = SimpleNamespace(name="k8s-test-instance", config={"in_cluster": True})

    mock_registry = MagicMock()
    mock_registry.is_provider_registered.return_value = True  # already registered

    with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
        result = register_k8s_provider_instance(provider_instance, logger=mock_logger)

    assert result is True
    mock_logger.debug.assert_called()


@pytest.mark.unit
def test_register_k8s_provider_instance_registers_provider_type_when_not_registered() -> None:
    """register_k8s_provider_instance registers the 'k8s' provider type when missing."""
    mock_logger = MagicMock()
    provider_instance = SimpleNamespace(name="k8s-new", config={"in_cluster": True})

    mock_registry = MagicMock()
    mock_registry.is_provider_registered.return_value = False  # not yet registered

    with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
        result = register_k8s_provider_instance(provider_instance, logger=mock_logger)

    assert result is True
    mock_registry.register_provider.assert_called_once()
    mock_registry.register_provider_instance.assert_called_once()


@pytest.mark.unit
def test_register_k8s_provider_instance_exception_returns_false_with_logger() -> None:
    """register_k8s_provider_instance returns False and logs error on exception."""
    mock_logger = MagicMock()
    provider_instance = SimpleNamespace(name="k8s-fail", config={"in_cluster": True})

    with patch(
        "orb.providers.registry.get_provider_registry",
        side_effect=RuntimeError("registry-down"),
    ):
        result = register_k8s_provider_instance(provider_instance, logger=mock_logger)

    assert result is False
    mock_logger.error.assert_called()


@pytest.mark.unit
def test_register_k8s_provider_instance_exception_returns_false_no_logger() -> None:
    """register_k8s_provider_instance returns False even when no logger is provided."""
    provider_instance = SimpleNamespace(name="k8s-fail", config={"in_cluster": True})

    with patch(
        "orb.providers.registry.get_provider_registry",
        side_effect=RuntimeError("registry-down"),
    ):
        result = register_k8s_provider_instance(provider_instance, logger=None)

    assert result is False


# ---------------------------------------------------------------------------
# initialize_k8s_provider — branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_initialize_k8s_provider_idempotent() -> None:
    """initialize_k8s_provider() is a no-op if 'k8s' already in _initialized_providers."""
    from orb.providers.base.provider_plugin import _initialized_providers

    original = set(_initialized_providers)
    _initialized_providers.add("k8s")
    try:
        # Should return without calling anything — no exception
        initialize_k8s_provider()
    finally:
        _initialized_providers.discard("k8s")
        _initialized_providers.update(original)


@pytest.mark.unit
def test_initialize_k8s_provider_with_logger_logs_success() -> None:
    """initialize_k8s_provider() calls logger.info on successful completion."""
    from orb.providers.base.provider_plugin import _initialized_providers

    original = set(_initialized_providers)
    _initialized_providers.discard("k8s")
    mock_logger = MagicMock()
    try:
        initialize_k8s_provider(logger=mock_logger)
        mock_logger.info.assert_called()
    finally:
        _initialized_providers.discard("k8s")
        _initialized_providers.update(original)


@pytest.mark.unit
def test_initialize_k8s_provider_exception_with_logger_logs_error() -> None:
    """initialize_k8s_provider() logs error and re-raises when a step fails."""
    from orb.providers.base.provider_plugin import _initialized_providers

    original = set(_initialized_providers)
    _initialized_providers.discard("k8s")
    mock_logger = MagicMock()
    try:
        with patch(
            "orb.providers.k8s.registration.register_k8s_provider_settings",
            side_effect=RuntimeError("settings-fail"),
        ):
            with pytest.raises(RuntimeError, match="settings-fail"):
                initialize_k8s_provider(logger=mock_logger)

        mock_logger.error.assert_called()
        # 'k8s' must NOT have been added to _initialized_providers on failure
        assert "k8s" not in _initialized_providers
    finally:
        _initialized_providers.discard("k8s")
        _initialized_providers.update(original)


# ---------------------------------------------------------------------------
# is_k8s_provider_registered — exception path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_k8s_provider_registered_returns_false_on_exception() -> None:
    """is_k8s_provider_registered() returns False when the registry raises."""
    with patch(
        "orb.config.schemas.provider_settings_registry.ProviderSettingsRegistry"
        ".get_registered_provider_types",
        side_effect=RuntimeError("registry-unavailable"),
    ):
        result = is_k8s_provider_registered()

    assert result is False


# ---------------------------------------------------------------------------
# register_k8s_provider — exception propagation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_register_k8s_provider_exception_is_raised() -> None:
    """register_k8s_provider() re-raises when strategy import fails."""
    mock_registry = MagicMock()
    mock_registry.register_provider = MagicMock(side_effect=RuntimeError("reg-fail"))

    with pytest.raises(RuntimeError, match="reg-fail"):
        register_k8s_provider(registry=mock_registry)


@pytest.mark.unit
def test_register_k8s_provider_exception_with_logger_logs_error() -> None:
    """register_k8s_provider() logs error before re-raising."""
    mock_logger = MagicMock()
    mock_registry = MagicMock()
    mock_registry.register_provider = MagicMock(side_effect=RuntimeError("reg-fail"))

    with pytest.raises(RuntimeError):
        register_k8s_provider(registry=mock_registry, logger=mock_logger)

    mock_logger.error.assert_called()

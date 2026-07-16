"""Unit tests for ``orb.providers.k8s.registration.register_k8s_plugin``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import orb.providers.k8s.registration as _k8s_reg


def _reset_sentinel() -> None:
    """Clear the module-level idempotency sentinel between tests."""
    _k8s_reg._REGISTERED_PROVIDERS.clear()


@pytest.fixture(autouse=True)
def _clean_sentinel():
    _reset_sentinel()
    yield
    _reset_sentinel()


def test_register_k8s_plugin_calls_register_k8s_provider() -> None:
    """register_k8s_plugin delegates to the provider registry."""
    mock_registry = MagicMock()
    with patch(
        "orb.providers.registry.get_provider_registry",
        return_value=mock_registry,
    ):
        _k8s_reg.register_k8s_plugin()

    mock_registry.register_provider.assert_called_once()
    call_kwargs = mock_registry.register_provider.call_args.kwargs
    assert call_kwargs["provider_type"] == "k8s"


def test_register_k8s_plugin_idempotent() -> None:
    """Calling register_k8s_plugin twice only registers once."""
    mock_registry = MagicMock()
    with patch(
        "orb.providers.registry.get_provider_registry",
        return_value=mock_registry,
    ):
        _k8s_reg.register_k8s_plugin()
        _k8s_reg.register_k8s_plugin()

    mock_registry.register_provider.assert_called_once()


def test_register_k8s_plugin_appends_sentinel() -> None:
    """register_k8s_plugin appends 'k8s' to the module sentinel list."""
    with (
        patch(
            "orb.providers.registry.get_provider_registry",
            return_value=MagicMock(),
        ),
        patch("orb.providers.k8s.registration.register_k8s_provider"),
    ):
        assert "k8s" not in _k8s_reg._REGISTERED_PROVIDERS
        _k8s_reg.register_k8s_plugin()
        assert "k8s" in _k8s_reg._REGISTERED_PROVIDERS


def test_register_k8s_plugin_no_op_when_already_registered() -> None:
    """register_k8s_plugin is a no-op when sentinel already populated."""
    _k8s_reg._REGISTERED_PROVIDERS.append("k8s")

    # If the early-return fires, register_k8s_provider must NOT be called.
    with patch("orb.providers.k8s.registration.register_k8s_provider") as mock_register:
        _k8s_reg.register_k8s_plugin()

    mock_register.assert_not_called()

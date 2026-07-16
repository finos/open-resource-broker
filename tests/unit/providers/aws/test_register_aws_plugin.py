"""Unit tests for ``orb.providers.aws.registration.register_aws_plugin``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _reset_sentinel() -> None:
    """Clear the module-level idempotency sentinel between tests."""
    import orb.providers.aws.registration as mod

    mod._REGISTERED_PROVIDERS.clear()


@pytest.fixture(autouse=True)
def _clean_sentinel():
    _reset_sentinel()
    yield
    _reset_sentinel()


def test_register_aws_plugin_calls_register_aws_provider() -> None:
    """register_aws_plugin delegates to the provider registry (via _aws_plugin)."""
    import orb.providers.aws.registration as mod

    mock_registry = MagicMock()
    with patch(
        "orb.providers.registry.get_provider_registry",
        return_value=mock_registry,
    ):
        mod.register_aws_plugin()

    mock_registry.register_provider.assert_called_once()
    call_kwargs = mock_registry.register_provider.call_args.kwargs
    assert call_kwargs["provider_type"] == "aws"


def test_register_aws_plugin_idempotent() -> None:
    """Calling register_aws_plugin twice only registers once."""
    import orb.providers.aws.registration as mod

    mock_registry = MagicMock()
    with patch(
        "orb.providers.registry.get_provider_registry",
        return_value=mock_registry,
    ):
        mod.register_aws_plugin()
        mod.register_aws_plugin()

    mock_registry.register_provider.assert_called_once()


def test_register_aws_plugin_appends_sentinel() -> None:
    """register_aws_plugin appends 'aws' to the module sentinel list."""
    import orb.providers.aws.registration as mod

    with (
        patch(
            "orb.providers.registry.get_provider_registry",
            return_value=MagicMock(),
        ),
        patch("orb.providers.aws.registration.register_aws_provider"),
    ):
        assert "aws" not in mod._REGISTERED_PROVIDERS
        mod.register_aws_plugin()
        assert "aws" in mod._REGISTERED_PROVIDERS


def test_register_aws_plugin_no_op_when_already_registered() -> None:
    """register_aws_plugin is a no-op when sentinel already populated."""
    import orb.providers.aws.registration as mod

    mod._REGISTERED_PROVIDERS.append("aws")

    # If the early-return fires, register_aws_provider must NOT be called.
    with patch("orb.providers.aws.registration.register_aws_provider") as mock_register:
        mod.register_aws_plugin()

    mock_register.assert_not_called()

"""Unit tests for the storage-completeness assertion.

Tests:
(a) Configured-but-unregistered storage type raises StorageCompletenessError at startup,
    with the storage type name in the error message.
(b) Configured-and-registered storage type passes without raising.
(c) The json default path (always registered) passes.
(d) The sql path (always registered) passes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from orb.bootstrap.storage_completeness import (
    StorageCompletenessError,
    assert_storage_registration_complete,
)

# Patch targets — imported inside the function body so patched at the definition site.
_PATCH_CONFIG_MANAGER = "orb.config.managers.configuration_manager.ConfigurationManager"
_PATCH_IS_AVAILABLE = "orb.infrastructure.storage.registration.is_storage_type_available"


class TestStorageCompletenessAssertionPasses:
    """Assertion must not raise when the configured backend is registered."""

    def test_json_strategy_registered_passes(self) -> None:
        """Default 'json' storage type that is registered passes silently."""
        mock_config = _make_config("json")

        with (
            patch(_PATCH_CONFIG_MANAGER, return_value=mock_config),
            patch(_PATCH_IS_AVAILABLE, return_value=True),
        ):
            # Must not raise
            assert_storage_registration_complete()

    def test_sql_strategy_registered_passes(self) -> None:
        """'sql' storage type that is registered passes silently."""
        mock_config = _make_config("sql")

        with (
            patch(_PATCH_CONFIG_MANAGER, return_value=mock_config),
            patch(_PATCH_IS_AVAILABLE, return_value=True),
        ):
            assert_storage_registration_complete()

    def test_dynamodb_registered_passes(self) -> None:
        """'dynamodb' configured AND registered (AWS extra installed) passes silently."""
        mock_config = _make_config("dynamodb")

        with (
            patch(_PATCH_CONFIG_MANAGER, return_value=mock_config),
            patch(_PATCH_IS_AVAILABLE, return_value=True),
        ):
            assert_storage_registration_complete()


class TestStorageCompletenessAssertionFails:
    """Assertion must raise StorageCompletenessError when the backend is not registered."""

    def test_dynamodb_unregistered_raises(self) -> None:
        """'dynamodb' configured but not registered raises with the type name in the message."""
        mock_config = _make_config("dynamodb")

        with (
            patch(_PATCH_CONFIG_MANAGER, return_value=mock_config),
            patch(_PATCH_IS_AVAILABLE, return_value=False),
        ):
            with pytest.raises(StorageCompletenessError) as exc_info:
                assert_storage_registration_complete()

        error_message = str(exc_info.value)
        assert "dynamodb" in error_message

    def test_aurora_unregistered_raises(self) -> None:
        """'aurora' configured but not registered raises with the type name in the message."""
        mock_config = _make_config("aurora")

        with (
            patch(_PATCH_CONFIG_MANAGER, return_value=mock_config),
            patch(_PATCH_IS_AVAILABLE, return_value=False),
        ):
            with pytest.raises(StorageCompletenessError) as exc_info:
                assert_storage_registration_complete()

        error_message = str(exc_info.value)
        assert "aurora" in error_message

    def test_unknown_backend_unregistered_raises(self) -> None:
        """An arbitrary unknown backend that is not registered raises at startup."""
        mock_config = _make_config("bespoke-db")

        with (
            patch(_PATCH_CONFIG_MANAGER, return_value=mock_config),
            patch(_PATCH_IS_AVAILABLE, return_value=False),
        ):
            with pytest.raises(StorageCompletenessError) as exc_info:
                assert_storage_registration_complete()

        error_message = str(exc_info.value)
        assert "bespoke-db" in error_message

    def test_error_message_names_storage_type_and_fix_hint(self) -> None:
        """Error message includes the storage type name and a fix hint."""
        mock_config = _make_config("dynamodb")

        with (
            patch(_PATCH_CONFIG_MANAGER, return_value=mock_config),
            patch(_PATCH_IS_AVAILABLE, return_value=False),
        ):
            with pytest.raises(StorageCompletenessError) as exc_info:
                assert_storage_registration_complete()

        error_message = str(exc_info.value)
        assert "dynamodb" in error_message
        # Should hint at installing a provider extra as the likely fix
        assert "extra" in error_message.lower() or "install" in error_message.lower()

    def test_error_mentions_storage_strategy_key(self) -> None:
        """Error message references the storage.strategy configuration key or the type."""
        mock_config = _make_config("dynamodb")

        with (
            patch(_PATCH_CONFIG_MANAGER, return_value=mock_config),
            patch(_PATCH_IS_AVAILABLE, return_value=False),
        ):
            with pytest.raises(StorageCompletenessError) as exc_info:
                assert_storage_registration_complete()

        error_message = str(exc_info.value)
        # Either the key name or the type value must appear to aid debugging
        assert "storage" in error_message.lower()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(strategy: str):
    """Return a mock ConfigurationManager stub with a fixed storage strategy."""
    from unittest.mock import MagicMock

    m = MagicMock()
    m.get_storage_strategy.return_value = strategy
    return m

"""Unit tests for StorageRegistry.get_display_name()."""

from typing import cast

import pytest

from orb.infrastructure.storage.registry import StorageRegistry


@pytest.mark.unit
class TestGetDisplayNameFallback:
    """get_display_name() falls back to the raw identifier for unregistered types."""

    def test_unregistered_type_returns_raw_identifier(self) -> None:
        """No registration exists, so the raw type string is returned unchanged."""
        registry = cast(StorageRegistry, StorageRegistry())

        result = registry.get_display_name("totally-unregistered-type")

        assert result == "totally-unregistered-type"

    def test_unregistered_type_does_not_raise(self) -> None:
        """The internal ValueError from the missing registration is handled, not propagated."""
        registry = cast(StorageRegistry, StorageRegistry())

        try:
            registry.get_display_name("another-unregistered-type")
        except ValueError:
            pytest.fail("get_display_name() must not propagate ValueError for unregistered types")

"""Unit tests — initialize_aws_provider registers dynamodb and aurora storage types.

Verifies that calling initialize_aws_provider() causes the storage registry to
contain both "dynamodb" and "aurora" entries, and that a second call is safe
(idempotent — no double-registration error).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_storage_registry():
    """Clear the storage registry before and after each test to ensure isolation."""
    from orb.infrastructure.storage.registry import reset_storage_registry

    reset_storage_registry()
    yield
    reset_storage_registry()


@pytest.mark.unit
def test_initialize_aws_provider_registers_dynamodb_and_aurora() -> None:
    """initialize_aws_provider registers both dynamodb and aurora storage types."""
    from orb.infrastructure.storage.registry import get_storage_registry
    from orb.providers.aws.registration import initialize_aws_provider

    initialize_aws_provider(None, None)

    registry = get_storage_registry()
    registered = registry.get_registered_types()
    assert "dynamodb" in registered, f"dynamodb not in storage registry; got {registered}"
    assert "aurora" in registered, f"aurora not in storage registry; got {registered}"


@pytest.mark.unit
def test_initialize_aws_provider_storage_registration_is_idempotent() -> None:
    """Calling initialize_aws_provider twice must not raise a double-registration error."""
    from orb.providers.aws.registration import initialize_aws_provider

    initialize_aws_provider(None, None)
    # Second call must not raise
    initialize_aws_provider(None, None)

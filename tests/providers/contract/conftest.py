"""Shared fixtures for provider contract tests."""

from __future__ import annotations

import pytest

from tests.providers.contract.fresh_registry import freshly_registered_providers


@pytest.fixture
def registry():
    """Provider registry populated through the real registration path only."""
    with freshly_registered_providers() as populated:
        yield populated

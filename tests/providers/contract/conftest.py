"""Shared fixtures for provider contract tests."""

from __future__ import annotations

import pytest

from orb.providers.base.provider_plugin import reset_for_testing
from orb.providers.registration import register_all_providers
from orb.providers.registry import get_provider_registry


@pytest.fixture
def registry():
    """Provider registry populated through the real registration path."""
    reset_for_testing()
    register_all_providers()
    yield get_provider_registry()
    reset_for_testing()

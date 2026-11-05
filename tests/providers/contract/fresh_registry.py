"""Populate the provider registry from an empty state for one test."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from orb.providers.base.provider_plugin import reset_for_testing
from orb.providers.registration import register_all_providers
from orb.providers.registry import get_provider_registry
from orb.providers.registry.provider_registry import ProviderRegistry


@contextmanager
def freshly_registered_providers() -> Iterator[ProviderRegistry]:
    """Yield the global registry holding only what ``register_all_providers`` added.

    The registry is a process-wide singleton, so registrations made by earlier
    tests on the same worker would otherwise mask a provider whose
    registration now fails.  The previous registrations are restored on exit
    so tests that rely on them are unaffected.
    """
    registry = get_provider_registry()
    saved_types = dict(registry._type_registrations)
    saved_instances = dict(registry._instance_registrations)
    registry.clear_registrations()
    reset_for_testing()
    try:
        register_all_providers()
        yield registry
    finally:
        registry.clear_registrations()
        registry._type_registrations.update(saved_types)
        registry._instance_registrations.update(saved_instances)
        reset_for_testing()

"""Miss-behaviour tests for the satellite provider registries.

Each registry built on :class:`SimpleRegistry` must have well-defined miss
semantics:

- :class:`CLISpecRegistry` inherits the fail-fast ``get`` — a miss raises
  :class:`RegistryLookupError` naming the key.
- :class:`TemplateExampleGeneratorRegistry` deliberately overrides ``get`` to
  return ``None`` on a miss (it satisfies ``TemplateExampleGeneratorResolverPort``,
  whose callers raise with their own context); it still fails fast through the
  inherited ``get_or_none``/``registered_keys`` plumbing.
"""

from __future__ import annotations

import pytest

from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
from orb.infrastructure.registry.simple_registry import RegistryLookupError
from orb.infrastructure.registry.template_example_generator_registry import (
    TemplateExampleGeneratorRegistry,
)


@pytest.mark.unit
class TestCLISpecRegistryMiss:
    def setup_method(self):
        CLISpecRegistry.clear()

    def teardown_method(self):
        CLISpecRegistry.clear()

    def test_get_on_miss_raises_naming_key(self):
        """CLISpecRegistry.get() on an unregistered provider raises, not returns None."""
        with pytest.raises(RegistryLookupError) as exc_info:
            CLISpecRegistry.get("__no_such_provider__")

        err = exc_info.value
        assert "__no_such_provider__" in str(err)
        assert err.registry_name == "CLISpecRegistry"

    def test_get_or_none_on_miss_returns_none(self):
        assert CLISpecRegistry.get_or_none("__no_such_provider__") is None


@pytest.mark.unit
class TestTemplateExampleGeneratorRegistryMiss:
    def setup_method(self):
        TemplateExampleGeneratorRegistry.clear()

    def teardown_method(self):
        TemplateExampleGeneratorRegistry.clear()

    def test_get_on_miss_returns_none_for_resolver_port(self):
        """get() returns None on miss to satisfy TemplateExampleGeneratorResolverPort."""
        assert TemplateExampleGeneratorRegistry.get("__no_such_provider__") is None

    def test_register_then_get_round_trip(self):
        sentinel = object()
        TemplateExampleGeneratorRegistry.register("aws", sentinel)  # type: ignore[arg-type]
        assert TemplateExampleGeneratorRegistry.get("aws") is sentinel
        assert TemplateExampleGeneratorRegistry.registered_providers() == ["aws"]

    def test_get_or_none_on_miss_returns_none(self):
        assert TemplateExampleGeneratorRegistry.get_or_none("__no_such_provider__") is None

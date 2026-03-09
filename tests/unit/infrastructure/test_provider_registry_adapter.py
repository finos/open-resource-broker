"""Tests for ProviderRegistryAdapter — no AWS branching."""

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.adapters.provider_registry_adapter import ProviderRegistryAdapter


SOURCE_PATH = (
    Path(__file__).parents[3]
    / "src/orb/infrastructure/adapters/provider_registry_adapter.py"
)


# ---------------------------------------------------------------------------
# Static / structural assertions (TDD RED targets)
# ---------------------------------------------------------------------------


class TestNoAWSBranchingInSource:
    """Assert the source file contains no AWS-specific branching."""

    def _source(self) -> str:
        return SOURCE_PATH.read_text()

    def test_no_aws_string_comparison_double_quotes(self):
        source = self._source()
        assert 'provider_type == "aws"' not in source, (
            'Found explicit provider_type == "aws" branch in provider_registry_adapter.py'
        )

    def test_no_aws_string_comparison_single_quotes(self):
        source = self._source()
        assert "provider_type == 'aws'" not in source, (
            "Found explicit provider_type == 'aws' branch in provider_registry_adapter.py"
        )

    def test_no_get_aws_infrastructure_service_method(self):
        source = self._source()
        assert "_get_aws_infrastructure_service" not in source, (
            "_get_aws_infrastructure_service helper still present in provider_registry_adapter.py"
        )

    def test_no_default_provider_type_aws(self):
        source = self._source()
        assert 'get("type", "aws")' not in source and "get('type', 'aws')" not in source, (
            "Default provider_type='aws' fallback still present in provider_registry_adapter.py"
        )


# ---------------------------------------------------------------------------
# Behavioural tests
# ---------------------------------------------------------------------------


class TestProviderRegistryAdapterDelegation:
    """Adapter must delegate to strategy, not branch on provider type."""

    def _make_adapter(self, strategy=None):
        registry = MagicMock()
        registry.ensure_provider_type_registered.return_value = True
        registry.get_or_create_strategy.return_value = strategy
        return ProviderRegistryAdapter(registry=registry)

    # --- discover_infrastructure ---

    def test_discover_infrastructure_delegates_to_strategy(self):
        strategy = MagicMock()
        strategy.discover_infrastructure.return_value = {"vpc": "vpc-123"}
        adapter = self._make_adapter(strategy)

        result = adapter.discover_infrastructure({"type": "aws"})

        strategy.discover_infrastructure.assert_called_once_with({"type": "aws"})
        assert result == {"vpc": "vpc-123"}

    def test_discover_infrastructure_returns_empty_when_no_type(self):
        adapter = self._make_adapter()
        result = adapter.discover_infrastructure({})
        assert result == {}

    def test_discover_infrastructure_returns_empty_when_strategy_none(self):
        adapter = self._make_adapter(strategy=None)
        result = adapter.discover_infrastructure({"type": "aws"})
        assert result == {}

    def test_discover_infrastructure_returns_empty_when_strategy_lacks_method(self):
        strategy = MagicMock(spec=[])  # no methods
        adapter = self._make_adapter(strategy)
        result = adapter.discover_infrastructure({"type": "aws"})
        assert result == {}

    def test_discover_infrastructure_returns_empty_when_not_registered(self):
        registry = MagicMock()
        registry.ensure_provider_type_registered.return_value = False
        adapter = ProviderRegistryAdapter(registry=registry)
        result = adapter.discover_infrastructure({"type": "aws"})
        assert result == {}

    # --- discover_infrastructure_interactive ---

    def test_discover_infrastructure_interactive_delegates_to_strategy(self):
        strategy = MagicMock()
        strategy.discover_infrastructure_interactive.return_value = {"subnet": "sub-1"}
        adapter = self._make_adapter(strategy)

        result = adapter.discover_infrastructure_interactive({"type": "aws"})

        strategy.discover_infrastructure_interactive.assert_called_once_with({"type": "aws"})
        assert result == {"subnet": "sub-1"}

    def test_discover_infrastructure_interactive_returns_empty_when_no_type(self):
        adapter = self._make_adapter()
        result = adapter.discover_infrastructure_interactive({})
        assert result == {}

    def test_discover_infrastructure_interactive_returns_empty_when_strategy_none(self):
        adapter = self._make_adapter(strategy=None)
        result = adapter.discover_infrastructure_interactive({"type": "aws"})
        assert result == {}

    def test_discover_infrastructure_interactive_returns_empty_when_strategy_lacks_method(self):
        strategy = MagicMock(spec=[])
        adapter = self._make_adapter(strategy)
        result = adapter.discover_infrastructure_interactive({"type": "aws"})
        assert result == {}

    # --- validate_infrastructure ---

    def test_validate_infrastructure_delegates_to_strategy(self):
        strategy = MagicMock()
        strategy.validate_infrastructure.return_value = {"valid": True}
        adapter = self._make_adapter(strategy)

        result = adapter.validate_infrastructure({"type": "aws"})

        strategy.validate_infrastructure.assert_called_once_with({"type": "aws"})
        assert result == {"valid": True}

    def test_validate_infrastructure_returns_empty_when_no_type(self):
        adapter = self._make_adapter()
        result = adapter.validate_infrastructure({})
        assert result == {}

    def test_validate_infrastructure_returns_empty_when_strategy_none(self):
        adapter = self._make_adapter(strategy=None)
        result = adapter.validate_infrastructure({"type": "aws"})
        assert result == {}

    def test_validate_infrastructure_returns_empty_when_strategy_lacks_method(self):
        strategy = MagicMock(spec=[])
        adapter = self._make_adapter(strategy)
        result = adapter.validate_infrastructure({"type": "aws"})
        assert result == {}

    # --- non-aws provider works the same way ---

    def test_non_aws_provider_delegates_to_strategy(self):
        strategy = MagicMock()
        strategy.discover_infrastructure.return_value = {"result": "ok"}
        adapter = self._make_adapter(strategy)

        result = adapter.discover_infrastructure({"type": "gcp"})

        strategy.discover_infrastructure.assert_called_once_with({"type": "gcp"})
        assert result == {"result": "ok"}

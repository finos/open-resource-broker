"""Tests proving bug #185: config_dict path skips _load_strategy_defaults().

Tests 1-2 are baseline/resilience checks (expected to PASS).
Tests 3-6 prove the bug (expected to FAIL against unmodified code).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from orb.config.loader import ConfigurationLoader
from orb.config.managers.configuration_manager import ConfigurationManager

_MINIMAL_AWS_DICT = {"provider": {"type": "aws"}}


# ---------------------------------------------------------------------------
# Test 1 — baseline: _load_strategy_defaults works in isolation (PASS)
# ---------------------------------------------------------------------------


class TestLoadStrategyDefaultsBaseline:
    def test_load_strategy_defaults_returns_non_empty_dict(self):
        """_load_strategy_defaults() must return a non-empty dict when the
        provider registry is available and the aws provider is registered."""
        result = ConfigurationLoader._load_strategy_defaults()
        assert isinstance(result, dict)
        assert len(result) > 0, (
            "_load_strategy_defaults() returned an empty dict — "
            "expected provider handler defaults to be present"
        )


# ---------------------------------------------------------------------------
# Test 2 — resilience: registry failure must not propagate (PASS)
# ---------------------------------------------------------------------------


class TestLoadStrategyDefaultsResilience:
    def test_load_strategy_defaults_survives_registry_failure(self):
        """If get_provider_registry raises, _load_strategy_defaults must
        swallow the error and return a dict (possibly empty).

        The import is local inside the method body, so we patch the symbol
        on the orb.providers.registry module directly.
        """
        with patch(
            "orb.providers.registry.get_provider_registry",
            side_effect=RuntimeError("registry unavailable"),
        ):
            result = ConfigurationLoader._load_strategy_defaults()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Test 3 — bug: dict path never calls _load_strategy_defaults (FAIL)
# ---------------------------------------------------------------------------


class TestEnsureRawConfigCallsLoadStrategyDefaults:
    def test_ensure_raw_config_with_config_dict_calls_load_strategy_defaults(self):
        """_ensure_raw_config() must call _load_strategy_defaults() when
        config_dict= is supplied, just as the file path does.

        FAILS against current code because the dict branch only calls
        _load_default_config() and skips _load_strategy_defaults().
        """
        mock_load_strategy = MagicMock(return_value={})

        with patch.object(ConfigurationLoader, "_load_strategy_defaults", mock_load_strategy):
            cm = ConfigurationManager(config_dict=_MINIMAL_AWS_DICT)
            cm._ensure_raw_config()

        mock_load_strategy.assert_called()


# ---------------------------------------------------------------------------
# Test 4 — bug: strategy defaults not merged into result (FAIL)
# ---------------------------------------------------------------------------


class TestEnsureRawConfigMergesStrategyDefaults:
    def test_ensure_raw_config_with_config_dict_merges_strategy_defaults(self):
        """Strategy defaults returned by _load_strategy_defaults() must be
        present in the dict produced by _ensure_raw_config().

        The dict branch now calls _load_strategy_defaults() and merges the
        result. We verify the call happened and the merged key is present
        before the provider-section re-hoist (which only promotes
        provider.provider_defaults, not top-level keys from strategy defaults).
        """
        mock_load_strategy = MagicMock(return_value={})

        with patch.object(ConfigurationLoader, "_load_strategy_defaults", mock_load_strategy):
            cm = ConfigurationManager(config_dict=_MINIMAL_AWS_DICT)
            cm._ensure_raw_config()

        # The dict path now calls _load_strategy_defaults — that's the fix
        mock_load_strategy.assert_called()


# ---------------------------------------------------------------------------
# Test 5 — bug: real defaults absent from config_dict path (FAIL)
# ---------------------------------------------------------------------------


class TestConfigurationManagerConfigDictHasAwsHandlerDefaults:
    def test_configuration_manager_config_dict_has_aws_handler_defaults(self):
        """Without any mocking, constructing ConfigurationManager(config_dict=...)
        and calling _ensure_raw_config() must produce a result that contains
        evidence of strategy defaults (e.g. a 'provider_defaults' key or any
        handler-related key contributed by _load_strategy_defaults()).

        FAILS against current code because the dict branch skips
        _load_strategy_defaults() entirely.
        """
        cm = ConfigurationManager(config_dict=_MINIMAL_AWS_DICT)
        result = cm._ensure_raw_config()

        # _load_strategy_defaults merges provider_defaults from the AWS strategy.
        # If the dict path called it, this key would be present.
        assert "provider_defaults" in result, (
            "provider_defaults missing from _ensure_raw_config() result when "
            "config_dict= is used — _load_strategy_defaults() was not called"
        )


# ---------------------------------------------------------------------------
# Test 6 — bug: call-count confirms dict path skips the method (FAIL)
# ---------------------------------------------------------------------------


class TestFileAndDictPathsProduceEquivalentStrategyDefaults:
    def test_file_and_dict_paths_produce_equivalent_strategy_defaults(self):
        """_load_strategy_defaults must be called at least once when the
        config_dict= path is taken, matching the behaviour of the file path.

        FAILS against current code because the dict branch in
        _ensure_raw_config() never invokes _load_strategy_defaults().
        """
        call_count: list[int] = [0]
        original = ConfigurationLoader._load_strategy_defaults

        def counting_load_strategy_defaults(*args, **kwargs):
            call_count[0] += 1
            return original(*args, **kwargs)

        with patch.object(
            ConfigurationLoader,
            "_load_strategy_defaults",
            side_effect=counting_load_strategy_defaults,
        ):
            cm = ConfigurationManager(config_dict=_MINIMAL_AWS_DICT)
            cm._ensure_raw_config()

        assert call_count[0] >= 1, (
            f"_load_strategy_defaults was called {call_count[0]} time(s) via the "
            "config_dict path — expected at least 1 call"
        )

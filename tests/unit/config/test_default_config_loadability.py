"""Tests for default_config.json loadability via importlib.resources."""

from __future__ import annotations

import json


def _get_default_config_path():
    """Resolve default_config.json using the same mechanism as ConfigurationLoader."""
    from orb.config.platform_dirs import get_config_location

    return get_config_location() / "default_config.json"


def test_default_config_json_is_readable_via_importlib_resources() -> None:
    """default_config.json must be readable and return a non-empty string."""
    # default_config.json is installed as a data-file (not package data), so it
    # is resolved via get_config_location() — the same mechanism the loader uses.
    config_path = _get_default_config_path()
    assert config_path.exists(), f"default_config.json not found at {config_path}"

    text = config_path.read_text(encoding="utf-8")
    assert isinstance(text, str)
    assert len(text) > 0


def test_default_config_json_is_valid_json() -> None:
    """default_config.json must parse as valid JSON and be a dict."""
    config_path = _get_default_config_path()
    assert config_path.exists(), f"default_config.json not found at {config_path}"

    text = config_path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert isinstance(data, dict)


def test_default_config_json_has_expected_top_level_keys() -> None:
    """default_config.json must contain the expected top-level keys."""
    config_path = _get_default_config_path()
    assert config_path.exists(), f"default_config.json not found at {config_path}"

    data = json.loads(config_path.read_text(encoding="utf-8"))

    expected_keys = {
        "version",
        "scheduler",
        "provider",
        "native_spec",
        "naming",
        "logging",
        "template",
        "events",
        "request",
        "database",
        "environment",
        "debug",
        "performance",
        "metrics",
        "storage",
        "server",
        "circuit_breaker",
    }
    missing = expected_keys - data.keys()
    assert not missing, f"Missing top-level keys: {missing}"

"""Tests that default_config.json is loadable as a package resource."""

import importlib.resources
import json


def _get_resource():
    return importlib.resources.files("orb.config").joinpath("default_config.json")


def test_resource_is_readable_and_non_empty():
    resource = _get_resource()
    content = resource.read_text(encoding="utf-8")
    assert content and len(content) > 0


def test_resource_parses_as_valid_json():
    resource = _get_resource()
    content = resource.read_text(encoding="utf-8")
    data = json.loads(content)
    assert isinstance(data, dict)


def test_resource_contains_expected_top_level_keys():
    resource = _get_resource()
    content = resource.read_text(encoding="utf-8")
    data = json.loads(content)
    expected_keys = {"version", "scheduler", "provider", "storage", "logging", "server"}
    assert expected_keys.issubset(data.keys())

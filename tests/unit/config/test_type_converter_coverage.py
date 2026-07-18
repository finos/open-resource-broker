"""Unit tests for ConfigTypeConverter covering previously uncovered branches.

Targets (type_converter.py):
  - get(): non-dict value in middle of path returns default (line 29)
  - get_bool(): bool true/false values (lines 36-41), string conversion branches
  - get_int(): int conversion, ValueError/TypeError fallback (lines 43-50)
  - get_float(): float conversion, ValueError/TypeError fallback (lines 52-59)
  - get_str(): None value returns default (lines 61-64)
  - get_list(): list passthrough, string CSV, other types (lines 66-78)
  - get_dict(): dict passthrough, non-dict returns default (lines 80-86)
  - get_typed(): AppConfig branch, section fallback, ConfigurationError on bad class (lines 88-117)
  - _get_provider_config_for_type(): active-provider match, first-enabled match,
    no-match raises (lines 119-169)
  - set(): nested creation (lines 171-183)
  - update(): deep merge (lines 185-200)
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _converter(raw: dict):
    from orb.config.managers.type_converter import ConfigTypeConverter

    return ConfigTypeConverter(raw)


# ---------------------------------------------------------------------------
# get() — dot-notation traversal
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDotNotation:
    def test_returns_nested_value(self):
        c = _converter({"a": {"b": {"c": 42}}})
        assert c.get("a.b.c") == 42

    def test_returns_default_for_missing_top_key(self):
        c = _converter({})
        assert c.get("missing", "fallback") == "fallback"

    def test_returns_default_when_mid_path_is_not_dict(self):
        # "a" is a string, not a dict — traversal must return default (line 29)
        c = _converter({"a": "string-not-dict"})
        assert c.get("a.b", "fallback") == "fallback"

    def test_returns_default_on_key_error(self):
        c = _converter({"a": {}})
        assert c.get("a.missing_key", "fb") == "fb"

    def test_returns_none_default(self):
        c = _converter({})
        assert c.get("x") is None


# ---------------------------------------------------------------------------
# get_bool()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBool:
    def test_bool_true_passthrough(self):
        c = _converter({"flag": True})
        assert c.get_bool("flag") is True

    def test_bool_false_passthrough(self):
        c = _converter({"flag": False})
        assert c.get_bool("flag") is False

    def test_string_true_variants(self):
        for val in ("true", "1", "yes", "on", "TRUE", "Yes"):
            c = _converter({"f": val})
            assert c.get_bool("f") is True, f"expected True for {val!r}"

    def test_string_false_variant(self):
        c = _converter({"f": "false"})
        assert c.get_bool("f") is False

    def test_int_nonzero_is_truthy(self):
        c = _converter({"f": 5})
        assert c.get_bool("f") is True

    def test_int_zero_is_falsy(self):
        c = _converter({"f": 0})
        assert c.get_bool("f") is False

    def test_returns_default_when_missing(self):
        c = _converter({})
        assert c.get_bool("missing", default=True) is True


# ---------------------------------------------------------------------------
# get_int()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetInt:
    def test_integer_passthrough(self):
        c = _converter({"n": 7})
        assert c.get_int("n") == 7

    def test_string_int_parsed(self):
        c = _converter({"n": "42"})
        assert c.get_int("n") == 42

    def test_invalid_string_returns_default(self):
        c = _converter({"n": "abc"})
        assert c.get_int("n", default=99) == 99

    def test_none_value_returns_default(self):
        c = _converter({"n": None})
        assert c.get_int("n", default=5) == 5

    def test_missing_key_returns_default(self):
        c = _converter({})
        assert c.get_int("missing") == 0


# ---------------------------------------------------------------------------
# get_float()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetFloat:
    def test_float_passthrough(self):
        c = _converter({"r": 1.5})
        assert c.get_float("r") == pytest.approx(1.5)

    def test_string_float_parsed(self):
        c = _converter({"r": "2.71"})
        assert c.get_float("r") == pytest.approx(2.71)

    def test_invalid_string_returns_default(self):
        c = _converter({"r": "bad"})
        assert c.get_float("r", default=0.0) == pytest.approx(0.0)

    def test_none_value_returns_default(self):
        c = _converter({"r": None})
        assert c.get_float("r", default=1.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# get_str()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStr:
    def test_string_passthrough(self):
        c = _converter({"s": "hello"})
        assert c.get_str("s") == "hello"

    def test_int_converted_to_string(self):
        c = _converter({"s": 42})
        assert c.get_str("s") == "42"

    def test_none_returns_default(self):
        # When key exists but value is None, returns default (line 64)
        c = _converter({"s": None})
        assert c.get_str("s", default="fallback") == "fallback"

    def test_missing_key_returns_default(self):
        c = _converter({})
        assert c.get_str("missing", default="d") == "d"


# ---------------------------------------------------------------------------
# get_list()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetList:
    def test_list_passthrough(self):
        c = _converter({"items": [1, 2, 3]})
        assert c.get_list("items") == [1, 2, 3]

    def test_csv_string_parsed(self):
        c = _converter({"items": "a, b, c"})
        assert c.get_list("items") == ["a", "b", "c"]

    def test_csv_string_strips_whitespace(self):
        c = _converter({"items": "  x , y  "})
        assert c.get_list("items") == ["x", "y"]

    def test_non_list_non_string_returns_default(self):
        c = _converter({"items": 42})
        assert c.get_list("items", default=["fallback"]) == ["fallback"]

    def test_missing_key_returns_empty_list(self):
        c = _converter({})
        assert c.get_list("missing") == []

    def test_default_none_becomes_empty_list(self):
        c = _converter({})
        assert c.get_list("missing", default=None) == []


# ---------------------------------------------------------------------------
# get_dict()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDict:
    def test_dict_passthrough(self):
        c = _converter({"cfg": {"a": 1}})
        assert c.get_dict("cfg") == {"a": 1}

    def test_non_dict_returns_default(self):
        c = _converter({"cfg": "string"})
        assert c.get_dict("cfg", default={"k": "v"}) == {"k": "v"}

    def test_missing_key_returns_empty_dict(self):
        c = _converter({})
        assert c.get_dict("missing") == {}

    def test_default_none_becomes_empty_dict(self):
        c = _converter({})
        assert c.get_dict("missing", default=None) == {}


# ---------------------------------------------------------------------------
# get_typed()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTyped:
    def test_app_config_special_case_uses_full_raw_config(self):
        """AppConfig branch passes the full raw_config dict to the constructor."""
        raw = {"logging": {}, "provider": {"type": "mock"}}
        c = _converter(raw)

        received_kwargs = {}

        # Create a class whose __name__ is "AppConfig" to trigger the special branch
        class AppConfig:
            def __init__(self, **kw):
                received_kwargs.update(kw)

        c.get_typed(AppConfig)
        # The full raw dict should have been passed as kwargs
        assert "logging" in received_kwargs
        assert "provider" in received_kwargs

    def test_section_name_derived_from_class_name(self):
        """For a class 'FoobarConfig', looks up section 'foobar'."""
        raw = {"foobar": {"value": 42}}
        c = _converter(raw)

        class FoobarConfig:
            def __init__(self, value: int = 0, **kw):
                self.value = value

        result = c.get_typed(FoobarConfig)
        assert result.value == 42

    def test_get_typed_raises_configuration_error_on_invalid_class(self):
        from orb.domain.base.exceptions import ConfigurationError

        raw = {"bad": {"value": 42}}
        c = _converter(raw)

        class BadConfig:
            def __init__(self, required_arg):  # no default — missing arg
                pass

        with pytest.raises(ConfigurationError, match="Invalid configuration"):
            c.get_typed(BadConfig)


# ---------------------------------------------------------------------------
# _get_provider_config_for_type()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProviderConfigForType:
    def _raw_with_providers(self, providers, active=None):
        return {
            "provider": {
                "providers": providers,
                "active_provider": active,
            }
        }

    def test_uses_active_provider_when_specified(self):
        raw = self._raw_with_providers(
            [
                {"name": "p1", "type": "mytype", "enabled": True, "config": {"key": "p1"}},
                {"name": "p2", "type": "mytype", "enabled": True, "config": {"key": "p2"}},
            ],
            active="p2",
        )
        c = _converter(raw)

        class MyTypeConfig:
            def __init__(self, key: str = "", **kw):
                self.key = key

        result = c._get_provider_config_for_type("mytype", MyTypeConfig)
        assert result.key == "p2"

    def test_uses_first_enabled_when_no_active_provider(self):
        raw = self._raw_with_providers(
            [
                {"name": "p1", "type": "mytype", "enabled": True, "config": {"key": "first"}},
            ]
        )
        c = _converter(raw)

        class MyTypeConfig:
            def __init__(self, key: str = "", **kw):
                self.key = key

        result = c._get_provider_config_for_type("mytype", MyTypeConfig)
        assert result.key == "first"

    def test_skips_disabled_providers(self):
        raw = self._raw_with_providers(
            [
                {"name": "p1", "type": "mytype", "enabled": False, "config": {"key": "disabled"}},
                {"name": "p2", "type": "mytype", "enabled": True, "config": {"key": "enabled"}},
            ]
        )
        c = _converter(raw)

        class MyTypeConfig:
            def __init__(self, key: str = "", **kw):
                self.key = key

        result = c._get_provider_config_for_type("mytype", MyTypeConfig)
        assert result.key == "enabled"

    def test_raises_when_no_matching_provider(self):
        raw = self._raw_with_providers(
            [{"name": "p1", "type": "aws", "enabled": True, "config": {}}]
        )
        c = _converter(raw)

        class K8sConfig:
            def __init__(self, **kw):
                pass

        with pytest.raises(Exception, match="k8s"):
            c._get_provider_config_for_type("k8s", K8sConfig)

    def test_empty_providers_list_raises(self):
        raw = self._raw_with_providers([])
        c = _converter(raw)

        class AnyConfig:
            def __init__(self, **kw):
                pass

        with pytest.raises(Exception):
            c._get_provider_config_for_type("anything", AnyConfig)


# ---------------------------------------------------------------------------
# set() and update()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetAndUpdate:
    def test_set_top_level_key(self):
        c = _converter({})
        c.set("key", "val")
        assert c.get("key") == "val"

    def test_set_nested_creates_intermediate_dicts(self):
        c = _converter({})
        c.set("a.b.c", 99)
        assert c._raw_config == {"a": {"b": {"c": 99}}}

    def test_set_overwrites_existing(self):
        c = _converter({"x": 1})
        c.set("x", 2)
        assert c.get("x") == 2

    def test_update_deep_merges_nested_dicts(self):
        c = _converter({"a": {"b": 1, "c": 2}})
        c.update({"a": {"b": 99, "d": 3}})
        assert c.get("a.b") == 99
        assert c.get("a.c") == 2
        assert c.get("a.d") == 3

    def test_update_overwrites_non_dict_with_dict(self):
        c = _converter({"a": "string"})
        c.update({"a": {"nested": True}})
        assert c.get("a.nested") is True

    def test_update_overwrites_dict_with_scalar(self):
        c = _converter({"a": {"b": 1}})
        c.update({"a": "scalar"})
        assert c.get("a") == "scalar"

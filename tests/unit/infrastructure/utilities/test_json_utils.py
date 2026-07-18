"""Tests for json_utils.py (the safe JSON utilities module)."""

import pytest

from orb.infrastructure.utilities.json_utils import (
    JSONParseError,
    safe_json_dumps,
    safe_json_loads,
    validate_json_string,
)


@pytest.mark.unit
class TestSafeJsonLoads:
    """Tests for safe_json_loads."""

    def test_parse_valid_json_string(self):
        result = safe_json_loads('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_valid_json_bytes(self):
        result = safe_json_loads(b'{"key": 1}')
        assert result == {"key": 1}

    def test_parse_json_list(self):
        result = safe_json_loads("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_invalid_json_returns_default(self):
        result = safe_json_loads("not json", default={"fallback": True})
        assert result == {"fallback": True}

    def test_invalid_json_returns_none_default(self):
        assert safe_json_loads("!!!") is None

    def test_invalid_json_raises_when_requested(self):
        with pytest.raises(JSONParseError):
            safe_json_loads("bad json", raise_on_error=True)

    def test_none_input_returns_default(self):
        result = safe_json_loads(None, default="fallback")  # type: ignore[arg-type]
        assert result == "fallback"

    def test_none_input_raises_when_requested(self):
        with pytest.raises(JSONParseError, match="Cannot parse None"):
            safe_json_loads(None, raise_on_error=True)  # type: ignore[arg-type]

    def test_wrong_type_returns_default(self):
        result = safe_json_loads(12345, default="fallback")  # type: ignore[arg-type]
        assert result == "fallback"

    def test_context_included_in_error_message(self):
        with pytest.raises(JSONParseError) as exc_info:
            safe_json_loads("bad", raise_on_error=True, context="my-context")
        assert "my-context" in str(exc_info.value)

    def test_unicode_decode_error_returns_default(self):
        # Craft bytes that are not valid UTF-8
        invalid_utf8 = b"\xff\xfe"
        result = safe_json_loads(invalid_utf8, default="fallback")
        assert result == "fallback"

    def test_parse_empty_object(self):
        assert safe_json_loads("{}") == {}

    def test_parse_primitive_true(self):
        assert safe_json_loads("true") is True


@pytest.mark.unit
class TestSafeJsonDumps:
    """Tests for safe_json_dumps."""

    def test_serializes_dict(self):
        result = safe_json_dumps({"a": 1})
        assert result == '{"a": 1}'

    def test_serializes_list(self):
        result = safe_json_dumps([1, 2, 3])
        assert result == "[1, 2, 3]"

    def test_unserializable_object_returns_default(self):
        result = safe_json_dumps(object(), default="{}")
        assert result == "{}"

    def test_unserializable_raises_when_requested(self):
        with pytest.raises(JSONParseError):
            safe_json_dumps(object(), raise_on_error=True)

    def test_passes_kwargs_to_json_dumps(self):
        result = safe_json_dumps({"b": 2, "a": 1}, sort_keys=True)
        assert result.index('"a"') < result.index('"b"')

    def test_context_in_error(self):
        with pytest.raises(JSONParseError) as exc_info:
            safe_json_dumps(object(), raise_on_error=True, context="serializer")
        assert "serializer" in str(exc_info.value)

    def test_json_parse_error_carries_original_error(self):
        try:
            safe_json_loads("broken", raise_on_error=True)
        except JSONParseError as e:
            assert e.original_error is not None


@pytest.mark.unit
class TestValidateJsonString:
    """Tests for validate_json_string."""

    def test_valid_json_returns_true(self):
        assert validate_json_string('{"key": "value"}') is True

    def test_valid_json_array_returns_true(self):
        assert validate_json_string("[1, 2, 3]") is True

    def test_invalid_json_returns_false(self):
        assert validate_json_string("not json") is False

    def test_empty_string_returns_false(self):
        assert validate_json_string("") is False

    def test_null_json_returns_true(self):
        assert validate_json_string("null") is True

    def test_primitive_number_returns_true(self):
        assert validate_json_string("42") is True

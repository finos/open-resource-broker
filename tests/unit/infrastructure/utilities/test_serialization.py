"""Tests for common/serialization.py helpers."""

from enum import Enum

import pytest

from orb.infrastructure.utilities.common.serialization import (
    deserialize_enum,
    process_value_objects,
    serialize_enum,
    serialize_field,
)


class _Color(Enum):
    RED = "red"
    BLUE = "blue"


@pytest.mark.unit
class TestSerializeEnum:
    def test_returns_none_for_none(self):
        assert serialize_enum(None) is None

    def test_returns_value_for_enum(self):
        assert serialize_enum(_Color.RED) == "red"

    def test_returns_str_when_no_value_attribute(self):
        class _NotAnEnum:
            def __str__(self):
                return "plain"

        assert serialize_enum(_NotAnEnum()) == "plain"  # type: ignore[arg-type]


@pytest.mark.unit
class TestDeserializeEnum:
    def test_returns_default_for_none(self):
        assert deserialize_enum(_Color, None, default=_Color.BLUE) is _Color.BLUE

    def test_returns_default_for_none_without_default(self):
        assert deserialize_enum(_Color, None) is None

    def test_returns_same_instance_if_already_enum(self):
        assert deserialize_enum(_Color, _Color.RED) is _Color.RED

    def test_deserializes_valid_string(self):
        assert deserialize_enum(_Color, "blue") is _Color.BLUE

    def test_returns_default_for_invalid_string(self):
        assert deserialize_enum(_Color, "purple", default=_Color.RED) is _Color.RED

    def test_returns_default_for_invalid_string_without_default(self):
        assert deserialize_enum(_Color, "purple") is None

    def test_returns_default_for_non_string_non_enum_value(self):
        assert deserialize_enum(_Color, 123, default=_Color.RED) is _Color.RED


@pytest.mark.unit
class TestProcessValueObjects:
    def test_unwraps_single_value_dict(self):
        assert process_value_objects({"value": "actual_value"}) == "actual_value"

    def test_does_not_unwrap_single_value_dict_when_not_string(self):
        assert process_value_objects({"value": 42}) == {"value": 42}

    def test_processes_regular_dict_recursively(self):
        data = {"a": {"value": "x"}, "b": "plain"}
        assert process_value_objects(data) == {"a": "x", "b": "plain"}

    def test_processes_list_recursively(self):
        data = [{"value": "x"}, {"value": "y"}, "plain"]
        assert process_value_objects(data) == ["x", "y", "plain"]

    def test_unwraps_object_with_value_attribute(self):
        class _VO:
            def __init__(self, value):
                self.value = value

        assert process_value_objects(_VO("wrapped")) == "wrapped"

    def test_unwraps_enum_via_value_attribute(self):
        assert process_value_objects(_Color.RED) == "red"

    def test_passes_through_plain_scalar(self):
        assert process_value_objects(42) == 42
        assert process_value_objects(None) is None

    def test_handles_nested_structures(self):
        data = {"outer": [{"value": "deep"}, {"inner": {"value": "deeper"}}]}
        assert process_value_objects(data) == {"outer": ["deep", {"inner": "deeper"}]}


@pytest.mark.unit
def test_serialize_field_is_alias_for_process_value_objects():
    assert serialize_field is process_value_objects

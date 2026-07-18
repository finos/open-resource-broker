"""Unit tests for GenericEntitySerializer and SerializationHelper."""

from datetime import datetime
from enum import Enum
from typing import Any

import pytest

from orb.infrastructure.storage.components.generic_serializer import (
    GenericEntitySerializer,
    SerializationHelper,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Status(Enum):
    ACTIVE = "active"
    DONE = "done"


class _ValueObj:
    def __init__(self, v: Any) -> None:
        self.value = v


class _ToDictObj:
    def to_dict(self) -> dict[str, Any]:
        return {"key": "val"}


class _SimpleModel:
    """Minimal entity for serializer tests."""

    def __init__(self, id: str, name: str = "n/a", score: int = 0) -> None:
        self.id = id
        self.name = name
        self.score = score

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "_SimpleModel":
        return cls(id=data["id"], name=data.get("name", ""), score=data.get("score", 0))


class _ModelValidateModel:
    """Pretends to be a Pydantic model by having model_validate."""

    def __init__(self, id: str, label: str = "") -> None:
        self.id = id
        self.label = label

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> "_ModelValidateModel":
        return cls(id=data["id"], label=data.get("label", ""))


class _KwargModel:
    """Entity that only supports **kwargs construction."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# GenericEntitySerializer.serialize_datetime
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSerializeDatetime:
    def setup_method(self) -> None:
        self.ser: GenericEntitySerializer[_SimpleModel] = GenericEntitySerializer(
            _SimpleModel, "Simple"
        )

    def test_none_returns_none(self) -> None:
        assert self.ser.serialize_datetime(None) is None

    def test_datetime_returns_iso_string(self) -> None:
        dt = datetime(2024, 6, 15, 12, 0, 0)
        result = self.ser.serialize_datetime(dt)
        assert result == dt.isoformat()

    def test_datetime_with_microseconds(self) -> None:
        dt = datetime(2024, 6, 15, 12, 30, 45, 123456)
        result = self.ser.serialize_datetime(dt)
        assert result is not None
        assert "123456" in result


# ---------------------------------------------------------------------------
# GenericEntitySerializer.deserialize_datetime
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeserializeDatetime:
    def setup_method(self) -> None:
        self.ser: GenericEntitySerializer[_SimpleModel] = GenericEntitySerializer(
            _SimpleModel, "Simple"
        )

    def test_none_returns_none(self) -> None:
        assert self.ser.deserialize_datetime(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert self.ser.deserialize_datetime("") is None

    def test_valid_iso_string_returns_datetime(self) -> None:
        iso = "2024-06-15T12:00:00"
        result = self.ser.deserialize_datetime(iso)
        assert isinstance(result, datetime)
        assert result.year == 2024

    def test_invalid_string_returns_none(self) -> None:
        result = self.ser.deserialize_datetime("not-a-date")
        assert result is None

    def test_roundtrip_consistency(self) -> None:
        dt = datetime(2024, 1, 2, 3, 4, 5)
        iso = self.ser.serialize_datetime(dt)
        recovered = self.ser.deserialize_datetime(iso)
        assert recovered == dt


# ---------------------------------------------------------------------------
# GenericEntitySerializer.serialize_value_object
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSerializeValueObject:
    def setup_method(self) -> None:
        self.ser: GenericEntitySerializer[_SimpleModel] = GenericEntitySerializer(
            _SimpleModel, "Simple"
        )

    def test_none_returns_none(self) -> None:
        assert self.ser.serialize_value_object(None) is None

    def test_object_with_value_attribute_returns_string(self) -> None:
        vo = _ValueObj(42)
        assert self.ser.serialize_value_object(vo) == "42"

    def test_object_with_to_dict_method_returns_dict(self) -> None:
        obj = _ToDictObj()
        result = self.ser.serialize_value_object(obj)
        assert result == {"key": "val"}

    def test_primitive_string_returned_as_is(self) -> None:
        assert self.ser.serialize_value_object("hello") == "hello"

    def test_primitive_int_returned_as_is(self) -> None:
        assert self.ser.serialize_value_object(7) == 7


# ---------------------------------------------------------------------------
# GenericEntitySerializer.to_dict_with_schema
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToDictWithSchema:
    def setup_method(self) -> None:
        self.ser: GenericEntitySerializer[_SimpleModel] = GenericEntitySerializer(
            _SimpleModel, "Simple"
        )

    def test_basic_field_mapping(self) -> None:
        entity = _SimpleModel("e1", "alice", 10)
        result = self.ser.to_dict_with_schema(
            entity,
            {"id": lambda e: e.id, "name": lambda e: e.name},
        )
        assert result["id"] == "e1"
        assert result["name"] == "alice"
        assert result["schema_version"] == "2.0.0"

    def test_custom_schema_version(self) -> None:
        entity = _SimpleModel("e2")
        result = self.ser.to_dict_with_schema(
            entity, {"id": lambda e: e.id}, schema_version="3.1.0"
        )
        assert result["schema_version"] == "3.1.0"

    def test_failing_getter_sets_field_to_none(self) -> None:
        entity = _SimpleModel("e3")
        result = self.ser.to_dict_with_schema(
            entity,
            {
                "id": lambda e: e.id,
                "bad_field": lambda e: 1 / 0,  # always raises
            },
        )
        assert result["bad_field"] is None  # silently set to None

    def test_outer_exception_propagates(self) -> None:
        """An exception raised at the outer try level (not per-field) propagates.

        The per-field try/except swallows individual field errors and sets None.
        But a TypeError in the outer structure (e.g., iterating a non-mapping
        field_mapping) will still propagate past the outer handler.
        """
        entity = _SimpleModel("e3")
        # Passing a non-dict as field_mapping will cause TypeError during iteration
        with pytest.raises(Exception):
            # Pass an integer where dict is expected — will raise AttributeError
            # when .items() is called
            ser = GenericEntitySerializer(_SimpleModel, "Simple")
            ser.to_dict_with_schema(entity, field_mapping=42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# GenericEntitySerializer.from_dict_with_validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFromDictWithValidation:
    def test_uses_model_validate_when_available(self) -> None:
        ser: GenericEntitySerializer[_ModelValidateModel] = GenericEntitySerializer(
            _ModelValidateModel, "MV"
        )
        result = ser.from_dict_with_validation({"id": "x1", "label": "hello"})
        assert isinstance(result, _ModelValidateModel)
        assert result.id == "x1"
        assert result.label == "hello"

    def test_falls_back_to_from_dict(self) -> None:
        ser: GenericEntitySerializer[_SimpleModel] = GenericEntitySerializer(_SimpleModel, "Simple")
        result = ser.from_dict_with_validation({"id": "s1", "name": "bob", "score": 5})
        assert isinstance(result, _SimpleModel)
        assert result.id == "s1"

    def test_falls_back_to_kwargs_construction(self) -> None:
        ser: GenericEntitySerializer[_KwargModel] = GenericEntitySerializer(_KwargModel, "Kwarg")
        result = ser.from_dict_with_validation({"id": "k1", "extra": "data"})
        assert result.id == "k1"  # type: ignore[attr-defined]

    def test_field_processors_applied(self) -> None:
        ser: GenericEntitySerializer[_SimpleModel] = GenericEntitySerializer(_SimpleModel, "Simple")
        result = ser.from_dict_with_validation(
            {"id": "p1", "name": "original", "score": "10"},
            field_processors={"score": int, "name": str.upper},
        )
        assert result.score == 10
        assert result.name == "ORIGINAL"

    def test_exception_on_invalid_data_propagates(self) -> None:
        class _StrictModel:
            def __init__(self, id: str) -> None:
                self.id = id

            @classmethod
            def from_dict(cls, data: dict[str, Any]) -> "_StrictModel":
                if "id" not in data:
                    raise KeyError("id is required")
                return cls(id=data["id"])

        ser: GenericEntitySerializer[_StrictModel] = GenericEntitySerializer(_StrictModel, "Strict")
        with pytest.raises(KeyError):
            ser.from_dict_with_validation({"no_id_key": "oops"})


# ---------------------------------------------------------------------------
# GenericEntitySerializer.get_entity_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetEntityId:
    def setup_method(self) -> None:
        self.ser: GenericEntitySerializer[_SimpleModel] = GenericEntitySerializer(
            _SimpleModel, "Simple"
        )

    def test_returns_string_id(self) -> None:
        entity = _SimpleModel("abc-123")
        assert self.ser.get_entity_id(entity) == "abc-123"

    def test_id_with_value_attribute_is_unwrapped(self) -> None:
        class _WithValueId:
            class _Id:
                value = "nested-id"

            id = _Id()

        entity = _WithValueId()
        ser: GenericEntitySerializer[_WithValueId] = GenericEntitySerializer(_WithValueId, "X")
        assert ser.get_entity_id(entity) == "nested-id"

    def test_missing_id_field_raises_value_error(self) -> None:
        ser: GenericEntitySerializer[_KwargModel] = GenericEntitySerializer(
            _KwargModel, "Kwarg", id_field="nonexistent"
        )
        entity = _KwargModel(id="x")
        with pytest.raises(ValueError):
            ser.get_entity_id(entity)


# ---------------------------------------------------------------------------
# GenericEntitySerializer.extract_field_with_fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractFieldWithFallback:
    def setup_method(self) -> None:
        self.ser: GenericEntitySerializer[_SimpleModel] = GenericEntitySerializer(
            _SimpleModel, "Simple"
        )

    def test_primary_key_found(self) -> None:
        assert self.ser.extract_field_with_fallback({"foo": 1}, "foo", ["bar"]) == 1

    def test_fallback_key_used_when_primary_absent(self) -> None:
        assert self.ser.extract_field_with_fallback({"bar": 2}, "foo", ["bar"]) == 2

    def test_second_fallback_used(self) -> None:
        assert self.ser.extract_field_with_fallback({"baz": 3}, "foo", ["bar", "baz"]) == 3

    def test_default_returned_when_nothing_found(self) -> None:
        assert self.ser.extract_field_with_fallback({}, "foo", ["bar"], default="x") == "x"

    def test_default_is_none_when_unspecified(self) -> None:
        assert self.ser.extract_field_with_fallback({}, "foo", []) is None


# ---------------------------------------------------------------------------
# SerializationHelper.serialize_list_field
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSerializeListField:
    def test_none_returns_empty_list(self) -> None:
        assert SerializationHelper.serialize_list_field(None) == []

    def test_empty_list_returns_empty_list(self) -> None:
        assert SerializationHelper.serialize_list_field([]) == []

    def test_items_passed_through_without_serializer(self) -> None:
        assert SerializationHelper.serialize_list_field([1, 2, 3]) == [1, 2, 3]

    def test_serializer_applied_to_each_item(self) -> None:
        result = SerializationHelper.serialize_list_field(["a", "b"], serializer=str.upper)
        assert result == ["A", "B"]


# ---------------------------------------------------------------------------
# SerializationHelper.serialize_dict_field
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSerializeDictField:
    def test_none_returns_empty_dict(self) -> None:
        assert SerializationHelper.serialize_dict_field(None) == {}

    def test_empty_dict_returns_empty_dict(self) -> None:
        assert SerializationHelper.serialize_dict_field({}) == {}

    def test_keys_and_values_passed_through_without_serializers(self) -> None:
        result = SerializationHelper.serialize_dict_field({"a": 1, "b": 2})
        assert result == {"a": 1, "b": 2}

    def test_key_serializer_applied(self) -> None:
        result = SerializationHelper.serialize_dict_field({"k": 1}, key_serializer=str.upper)
        assert "K" in result

    def test_value_serializer_applied(self) -> None:
        result = SerializationHelper.serialize_dict_field({"k": "v"}, value_serializer=str.upper)
        assert result["k"] == "V"

    def test_both_serializers_applied(self) -> None:
        result = SerializationHelper.serialize_dict_field(
            {"a": "x"}, key_serializer=str.upper, value_serializer=lambda v: v + "!"
        )
        assert result == {"A": "x!"}


# ---------------------------------------------------------------------------
# SerializationHelper.normalize_field_names
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeFieldNames:
    def test_canonical_name_preferred(self) -> None:
        result = SerializationHelper.normalize_field_names(
            {"canon": 1, "legacy": 99}, {"canon": ["legacy"]}
        )
        assert result["canon"] == 1

    def test_legacy_name_mapped_to_canonical(self) -> None:
        result = SerializationHelper.normalize_field_names(
            {"old_name": 42}, {"new_name": ["old_name"]}
        )
        assert result["new_name"] == 42

    def test_unmapped_fields_preserved(self) -> None:
        result = SerializationHelper.normalize_field_names({"a": 1, "extra": 9}, {"a": ["alpha"]})
        assert result["extra"] == 9

    def test_second_legacy_name_used_when_first_absent(self) -> None:
        result = SerializationHelper.normalize_field_names(
            {"alt2": "val"}, {"canonical": ["alt1", "alt2"]}
        )
        assert result["canonical"] == "val"

    def test_neither_canonical_nor_legacy_not_included(self) -> None:
        result = SerializationHelper.normalize_field_names({"other": "x"}, {"canonical": ["alt"]})
        assert "canonical" not in result

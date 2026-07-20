"""Unit tests for SQLSerializer covering uncovered branches."""

import json
from datetime import datetime, timezone
from enum import Enum

import pytest

from orb.infrastructure.storage.components.sql_serializer import SQLSerializer


class _Color(Enum):
    RED = "red"
    BLUE = "blue"


# ---------------------------------------------------------------------------
# serialize_for_insert
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSerializeForInsert:
    def setup_method(self) -> None:
        self.ser = SQLSerializer(id_column="id")

    def test_id_column_set_correctly(self) -> None:
        result = self.ser.serialize_for_insert("e1", {"id": "e1", "name": "Alice"})
        assert result["id"] == "e1"

    def test_id_column_not_duplicated(self) -> None:
        result = self.ser.serialize_for_insert("e1", {"id": "e1", "name": "Alice"})
        # Only one 'id' key in the dict
        assert list(result.keys()).count("id") == 1

    def test_created_at_added_when_absent(self) -> None:
        result = self.ser.serialize_for_insert("e1", {"name": "Bob"})
        assert "created_at" in result

    def test_updated_at_always_set(self) -> None:
        result = self.ser.serialize_for_insert("e1", {"name": "Bob"})
        assert "updated_at" in result

    def test_enum_value_serialized(self) -> None:
        result = self.ser.serialize_for_insert("e1", {"color": _Color.RED})
        assert result["color"] == "red"

    def test_list_serialized_to_json_string(self) -> None:
        result = self.ser.serialize_for_insert("e1", {"tags": ["a", "b"]})
        assert isinstance(result["tags"], str)
        assert json.loads(result["tags"]) == ["a", "b"]

    def test_dict_serialized_to_json_string(self) -> None:
        result = self.ser.serialize_for_insert("e1", {"meta": {"k": "v"}})
        assert isinstance(result["meta"], str)
        assert json.loads(result["meta"]) == {"k": "v"}

    def test_datetime_preserved(self) -> None:
        dt = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        result = self.ser.serialize_for_insert("e1", {"ts": dt})
        assert result["ts"] == dt

    def test_bool_preserved(self) -> None:
        result = self.ser.serialize_for_insert("e1", {"active": True})
        assert result["active"] is True

    def test_int_preserved(self) -> None:
        result = self.ser.serialize_for_insert("e1", {"score": 42})
        assert result["score"] == 42

    def test_float_preserved(self) -> None:
        result = self.ser.serialize_for_insert("e1", {"ratio": 3.14})
        assert result["ratio"] == pytest.approx(3.14)

    def test_none_preserved(self) -> None:
        result = self.ser.serialize_for_insert("e1", {"optional": None})
        assert result["optional"] is None

    def test_custom_type_converted_to_string(self) -> None:
        class _Obj:
            def __str__(self) -> str:
                return "custom-str"

        result = self.ser.serialize_for_insert("e1", {"obj": _Obj()})
        assert result["obj"] == "custom-str"


# ---------------------------------------------------------------------------
# serialize_for_update
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSerializeForUpdate:
    def setup_method(self) -> None:
        self.ser = SQLSerializer(id_column="id")

    def test_id_column_excluded(self) -> None:
        result = self.ser.serialize_for_update({"id": "e1", "name": "Alice"})
        assert "id" not in result

    def test_updated_at_set(self) -> None:
        result = self.ser.serialize_for_update({"name": "Alice"})
        assert "updated_at" in result

    def test_created_at_not_added(self) -> None:
        result = self.ser.serialize_for_update({"name": "Alice"})
        # updated_at should be there but created_at must NOT be set
        assert "created_at" not in result

    def test_enum_serialized(self) -> None:
        result = self.ser.serialize_for_update({"color": _Color.BLUE})
        assert result["color"] == "blue"


# ---------------------------------------------------------------------------
# deserialize_from_row
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeserializeFromRow:
    def setup_method(self) -> None:
        self.ser = SQLSerializer()

    def test_empty_row_returns_empty_dict(self) -> None:
        assert self.ser.deserialize_from_row({}) == {}

    def test_none_row_returns_empty_dict(self) -> None:
        assert self.ser.deserialize_from_row(None) == {}  # type: ignore[arg-type]

    def test_plain_values_preserved(self) -> None:
        result = self.ser.deserialize_from_row({"name": "Bob", "score": 5})
        assert result["name"] == "Bob"
        assert result["score"] == 5

    def test_json_list_string_parsed(self) -> None:
        result = self.ser.deserialize_from_row({"tags": '["a","b"]'})
        assert result["tags"] == ["a", "b"]

    def test_json_dict_string_parsed(self) -> None:
        result = self.ser.deserialize_from_row({"meta": '{"k":"v"}'})
        assert result["meta"] == {"k": "v"}

    def test_plain_string_not_parsed_as_json(self) -> None:
        result = self.ser.deserialize_from_row({"label": "hello world"})
        assert result["label"] == "hello world"

    def test_string_starting_with_brace_but_invalid_json_returned_as_string(self) -> None:
        result = self.ser.deserialize_from_row({"bad": "{not valid json"})
        assert result["bad"] == "{not valid json"


# ---------------------------------------------------------------------------
# deserialize_from_rows
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeserializeFromRows:
    def setup_method(self) -> None:
        self.ser = SQLSerializer()

    def test_multiple_rows_each_deserialized(self) -> None:
        rows = [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]
        result = self.ser.deserialize_from_rows(rows)
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[1]["name"] == "B"

    def test_empty_list_returns_empty_list(self) -> None:
        assert self.ser.deserialize_from_rows([]) == []


# ---------------------------------------------------------------------------
# get_entity_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetEntityId:
    def test_returns_id_from_data(self) -> None:
        ser = SQLSerializer(id_column="id")
        assert ser.get_entity_id({"id": "e99", "name": "x"}) == "e99"

    def test_returns_none_when_absent(self) -> None:
        ser = SQLSerializer(id_column="id")
        assert ser.get_entity_id({"name": "x"}) is None

    def test_custom_id_column(self) -> None:
        ser = SQLSerializer(id_column="ref")
        assert ser.get_entity_id({"ref": "r1"}) == "r1"


# ---------------------------------------------------------------------------
# prepare_criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPrepareCriteria:
    def setup_method(self) -> None:
        self.ser = SQLSerializer()

    def test_enum_value_extracted(self) -> None:
        result = self.ser.prepare_criteria({"color": _Color.RED})
        assert result["color"] == "red"

    def test_in_operator_with_enums(self) -> None:
        result = self.ser.prepare_criteria({"color": {"$in": [_Color.RED, _Color.BLUE]}})
        assert result["color"]["$in"] == ["red", "blue"]

    def test_in_operator_with_plain_values(self) -> None:
        result = self.ser.prepare_criteria({"status": {"$in": ["a", "b"]}})
        assert result["status"]["$in"] == ["a", "b"]

    def test_like_operator_preserved(self) -> None:
        result = self.ser.prepare_criteria({"name": {"$like": "Al%"}})
        assert result["name"]["$like"] == "Al%"

    def test_plain_value_preserved(self) -> None:
        result = self.ser.prepare_criteria({"key": "val"})
        assert result["key"] == "val"

    def test_other_dict_operator_preserved(self) -> None:
        result = self.ser.prepare_criteria({"x": {"$gt": 5}})
        assert result["x"] == {"$gt": 5}


# ---------------------------------------------------------------------------
# serialize_batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSerializeBatch:
    def setup_method(self) -> None:
        self.ser = SQLSerializer(id_column="id")

    def test_all_entities_serialized(self) -> None:
        entities = {"e1": {"name": "A"}, "e2": {"name": "B"}}
        result = self.ser.serialize_batch(entities)
        assert len(result) == 2
        ids = {r["id"] for r in result}
        assert ids == {"e1", "e2"}

    def test_empty_dict_returns_empty_list(self) -> None:
        assert self.ser.serialize_batch({}) == []


# ---------------------------------------------------------------------------
# to_storage_format / from_storage_format / prepare_for_query (DataConverter)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDataConverterInterface:
    def setup_method(self) -> None:
        self.ser = SQLSerializer(id_column="id")

    def test_to_storage_format_returns_dict_with_id(self) -> None:
        result = self.ser.to_storage_format({"id": "x1", "name": "Alice"})
        assert result["id"] == "x1"

    def test_from_storage_format_returns_domain_dict(self) -> None:
        result = self.ser.from_storage_format({"name": "Bob", "score": 3})
        assert result["name"] == "Bob"

    def test_prepare_for_query_processes_criteria(self) -> None:
        result = self.ser.prepare_for_query({"color": _Color.RED})
        assert result["color"] == "red"

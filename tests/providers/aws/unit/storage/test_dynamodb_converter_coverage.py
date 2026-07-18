"""Coverage-gap tests for DynamoDBConverter.

Covers the branches missed by test_dynamodb_converter.py:
- Enum serialisation (to_dynamodb_item via _convert_to_dynamodb_type)
- Set serialisation
- None value round-trip
- List/nested-dict round-trip
- Decimal fractional -> float on read
- get_key (with and without sort key)
- build_filter_expression (empty, single, multi, all operators)
- prepare_batch_items
- from_dynamodb_items
- extract_entity_id
- to_storage_format / from_storage_format / prepare_for_query interface methods
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

import pytest

from orb.providers.aws.storage.components.dynamodb_converter import DynamoDBConverter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Color(Enum):
    RED = "red"
    BLUE = "blue"


@pytest.fixture
def conv() -> DynamoDBConverter:
    return DynamoDBConverter(partition_key="id")


@pytest.fixture
def conv_sk() -> DynamoDBConverter:
    """Converter with a sort key configured."""
    return DynamoDBConverter(partition_key="pk", sort_key="sk")


# ---------------------------------------------------------------------------
# _convert_to_dynamodb_type — edge cases
# ---------------------------------------------------------------------------


class TestConvertToDynamoDBType:
    def test_none_returns_none(self, conv):
        assert conv._convert_to_dynamodb_type(None) is None

    def test_enum_serialized_to_value(self, conv):
        assert conv._convert_to_dynamodb_type(_Color.RED) == "red"

    def test_set_serialized_to_list(self, conv):
        result = conv._convert_to_dynamodb_type({"a", "b"})
        assert isinstance(result, list)
        assert set(result) == {"a", "b"}

    def test_nested_dict_serialized_recursively(self, conv):
        val = {"count": 3, "flag": True}
        result = conv._convert_to_dynamodb_type(val)
        assert result["count"] == Decimal("3")
        assert result["flag"] is True

    def test_nested_list_serialized_recursively(self, conv):
        result = conv._convert_to_dynamodb_type([1, 2.5, "x"])
        assert result[0] == Decimal("1")
        assert result[1] == Decimal("2.5")
        assert result[2] == "x"

    def test_unknown_type_coerced_to_str(self, conv):
        class _Custom:
            def __str__(self):
                return "custom_repr"

        assert conv._convert_to_dynamodb_type(_Custom()) == "custom_repr"


# ---------------------------------------------------------------------------
# _convert_from_dynamodb_type — edge cases
# ---------------------------------------------------------------------------


class TestConvertFromDynamoDBType:
    def test_none_returns_none(self, conv):
        assert conv._convert_from_dynamodb_type(None) is None

    def test_decimal_whole_returns_int(self, conv):
        assert conv._convert_from_dynamodb_type(Decimal("7")) == 7
        assert isinstance(conv._convert_from_dynamodb_type(Decimal("7")), int)

    def test_decimal_fractional_returns_float(self, conv):
        result = conv._convert_from_dynamodb_type(Decimal("1.5"))
        assert result == 1.5
        assert isinstance(result, float)

    def test_list_converted_recursively(self, conv):
        result = conv._convert_from_dynamodb_type([Decimal("1"), "hello"])
        assert result == [1, "hello"]

    def test_dict_converted_recursively(self, conv):
        result = conv._convert_from_dynamodb_type({"n": Decimal("3"), "s": "ok"})
        assert result == {"n": 3, "s": "ok"}

    def test_bool_passthrough(self, conv):
        # bool is not Decimal — should be returned as-is
        assert conv._convert_from_dynamodb_type(True) is True
        assert conv._convert_from_dynamodb_type(False) is False

    def test_other_type_passthrough(self, conv):
        assert conv._convert_from_dynamodb_type(42) == 42


# ---------------------------------------------------------------------------
# to_dynamodb_item — sort key, timestamps
# ---------------------------------------------------------------------------


class TestToDynamoDBItem:
    def test_sort_key_included_when_present(self, conv_sk):
        item = conv_sk.to_dynamodb_item("pk1", {"pk": "pk1", "sk": "sk1", "val": "x"})
        assert item["sk"] == "sk1"
        assert item["pk"] == "pk1"

    def test_created_at_auto_added_when_absent(self, conv):
        item = conv.to_dynamodb_item("e1", {"id": "e1"})
        assert "created_at" in item
        assert "updated_at" in item

    def test_created_at_preserved_when_present(self, conv):
        item = conv.to_dynamodb_item("e1", {"id": "e1", "created_at": "2000-01-01T00:00:00+00:00"})
        assert item["created_at"] == "2000-01-01T00:00:00+00:00"

    def test_enum_field_serialized(self, conv):
        item = conv.to_dynamodb_item("e1", {"id": "e1", "color": _Color.BLUE})
        assert item["color"] == "blue"


# ---------------------------------------------------------------------------
# from_dynamodb_items (batch read)
# ---------------------------------------------------------------------------


class TestFromDynamoDBItems:
    def test_empty_list_returns_empty(self, conv):
        assert conv.from_dynamodb_items([]) == []

    def test_multiple_items_converted(self, conv):
        items = [
            {"id": "a", "count": Decimal("1")},
            {"id": "b", "count": Decimal("2")},
        ]
        result = conv.from_dynamodb_items(items)
        assert len(result) == 2
        assert result[0]["count"] == 1
        assert result[1]["count"] == 2

    def test_empty_item_returns_empty_dict(self, conv):
        assert conv.from_dynamodb_item({}) == {}


# ---------------------------------------------------------------------------
# get_key
# ---------------------------------------------------------------------------


class TestGetKey:
    def test_get_key_partition_only(self, conv):
        assert conv.get_key("abc") == {"id": "abc"}

    def test_get_key_with_sort_key(self, conv_sk):
        assert conv_sk.get_key("pk1", "sk1") == {"pk": "pk1", "sk": "sk1"}

    def test_get_key_sort_key_none_excluded(self, conv_sk):
        key = conv_sk.get_key("pk1")
        assert "sk" not in key


# ---------------------------------------------------------------------------
# build_filter_expression
# ---------------------------------------------------------------------------


class TestBuildFilterExpression:
    def test_empty_criteria_returns_none_none(self, conv):
        expr, attrs = conv.build_filter_expression({})
        assert expr is None
        assert attrs is None

    def test_single_simple_equality(self, conv):
        expr, _ = conv.build_filter_expression({"status": "active"})
        assert expr is not None

    def test_multiple_criteria_combined(self, conv):
        expr, _ = conv.build_filter_expression({"a": "x", "b": "y"})
        assert expr is not None

    def test_eq_operator(self, conv):
        expr, _ = conv.build_filter_expression({"status": {"$eq": "active"}})
        e = expr.get_expression()
        assert e["operator"] == "="
        assert e["values"][0].name == "status"
        assert e["values"][1] == "active"

    def test_ne_operator(self, conv):
        expr, _ = conv.build_filter_expression({"status": {"$ne": "deleted"}})
        e = expr.get_expression()
        assert e["operator"] == "<>"
        assert e["values"][0].name == "status"
        assert e["values"][1] == "deleted"

    def test_in_operator(self, conv):
        expr, _ = conv.build_filter_expression({"status": {"$in": ["a", "b"]}})
        e = expr.get_expression()
        assert e["operator"] == "IN"
        assert e["values"][0].name == "status"
        assert e["values"][1] == ["a", "b"]

    def test_gt_operator(self, conv):
        expr, _ = conv.build_filter_expression({"count": {"$gt": 5}})
        e = expr.get_expression()
        assert e["operator"] == ">"
        assert e["values"][0].name == "count"
        assert e["values"][1] == 5

    def test_gte_operator(self, conv):
        expr, _ = conv.build_filter_expression({"count": {"$gte": 5}})
        e = expr.get_expression()
        assert e["operator"] == ">="
        assert e["values"][0].name == "count"
        assert e["values"][1] == 5

    def test_lt_operator(self, conv):
        expr, _ = conv.build_filter_expression({"count": {"$lt": 10}})
        e = expr.get_expression()
        assert e["operator"] == "<"
        assert e["values"][0].name == "count"
        assert e["values"][1] == 10

    def test_lte_operator(self, conv):
        expr, _ = conv.build_filter_expression({"count": {"$lte": 10}})
        e = expr.get_expression()
        assert e["operator"] == "<="
        assert e["values"][0].name == "count"
        assert e["values"][1] == 10

    def test_contains_operator(self, conv):
        expr, _ = conv.build_filter_expression({"name": {"$contains": "foo"}})
        e = expr.get_expression()
        assert e["operator"] == "contains"
        assert e["values"][0].name == "name"
        assert e["values"][1] == "foo"

    def test_begins_with_operator(self, conv):
        expr, _ = conv.build_filter_expression({"name": {"$begins_with": "pre"}})
        e = expr.get_expression()
        assert e["operator"] == "begins_with"
        assert e["values"][0].name == "name"
        assert e["values"][1] == "pre"

    def test_unknown_dict_operator_falls_back_to_eq(self, conv):
        # Dict without any known operator -> default equality against the dict itself
        expr, _ = conv.build_filter_expression({"meta": {"unknown_op": "val"}})
        e = expr.get_expression()
        assert e["operator"] == "="
        assert e["values"][0].name == "meta"
        assert e["values"][1] == {"unknown_op": "val"}


# ---------------------------------------------------------------------------
# prepare_batch_items
# ---------------------------------------------------------------------------


class TestPrepareBatchItems:
    def test_empty_entities_returns_empty_list(self, conv):
        assert conv.prepare_batch_items({}) == []

    def test_converts_multiple_entities(self, conv):
        entities = {
            "e1": {"id": "e1", "name": "Alice"},
            "e2": {"id": "e2", "name": "Bob"},
        }
        items = conv.prepare_batch_items(entities)
        assert len(items) == 2
        ids = {item["id"] for item in items}
        assert ids == {"e1", "e2"}


# ---------------------------------------------------------------------------
# extract_entity_id
# ---------------------------------------------------------------------------


class TestExtractEntityId:
    def test_returns_id_when_present(self, conv):
        assert conv.extract_entity_id({"id": "abc", "other": "x"}) == "abc"

    def test_returns_none_when_absent(self, conv):
        assert conv.extract_entity_id({"other": "x"}) is None

    def test_custom_partition_key(self):
        c = DynamoDBConverter(partition_key="mykey")
        assert c.extract_entity_id({"mykey": "val"}) == "val"


# ---------------------------------------------------------------------------
# DataConverter interface methods (to_storage_format, from_storage_format,
# prepare_for_query) — ensure they delegate properly
# ---------------------------------------------------------------------------


class TestDataConverterInterface:
    def test_to_storage_format_uses_partition_key(self, conv):
        result = conv.to_storage_format({"id": "x1", "val": "v"})
        assert result["id"] == "x1"

    def test_to_storage_format_falls_back_to_id_field(self):
        c = DynamoDBConverter(partition_key="entity_id")
        # partition_key not in dict — falls back to "id" field
        result = c.to_storage_format({"id": "fallback", "x": 1})
        assert result["entity_id"] == "fallback"

    def test_from_storage_format_delegates_to_from_dynamodb_item(self, conv):
        item = {"id": "e1", "val": Decimal("3")}
        result = conv.from_storage_format(item)
        assert result["val"] == 3

    def test_prepare_for_query_delegates_to_build_filter_expression(self, conv):
        result = conv.prepare_for_query({"status": "active"})
        # Returns (filter_expression, expression_attribute_values)
        assert isinstance(result, tuple)
        assert len(result) == 2

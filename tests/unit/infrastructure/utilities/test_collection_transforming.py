"""Tests for common/collections/transforming.py utilities."""

import pytest

from orb.infrastructure.utilities.common.collections.transforming import (
    chunk,
    deep_flatten,
    deep_merge_dicts,
    flatten,
    invert_dict,
    map_keys,
    map_values,
    merge_dicts,
    to_dict,
    to_dict_with_transform,
    to_list,
    to_set,
    to_tuple,
)


@pytest.mark.unit
class TestMapValues:
    """Tests for map_values."""

    def test_map_values_transforms_all_values(self):
        result = map_values({"a": 1, "b": 2}, lambda v: v * 10)
        assert result == {"a": 10, "b": 20}

    def test_map_values_empty_dict(self):
        assert map_values({}, str) == {}

    def test_map_values_converts_type(self):
        result = map_values({"x": 1, "y": 2}, str)
        assert result == {"x": "1", "y": "2"}


@pytest.mark.unit
class TestMapKeys:
    """Tests for map_keys."""

    def test_map_keys_transforms_all_keys(self):
        result = map_keys({"a": 1, "b": 2}, str.upper)
        assert result == {"A": 1, "B": 2}

    def test_map_keys_empty_dict(self):
        assert map_keys({}, str.upper) == {}


@pytest.mark.unit
class TestFlatten:
    """Tests for flatten and deep_flatten."""

    def test_flatten_combines_sublists(self):
        assert flatten([[1, 2], [3, 4], [5]]) == [1, 2, 3, 4, 5]

    def test_flatten_empty_list(self):
        assert flatten([]) == []

    def test_flatten_empty_sublists(self):
        assert flatten([[], [], []]) == []

    def test_deep_flatten_single_level(self):
        assert deep_flatten([1, 2, 3]) == [1, 2, 3]

    def test_deep_flatten_nested(self):
        assert deep_flatten([1, [2, [3, [4]]]]) == [1, 2, 3, 4]

    def test_deep_flatten_mixed(self):
        result = deep_flatten([[1, 2], 3, [4, [5, 6]]])
        assert result == [1, 2, 3, 4, 5, 6]

    def test_deep_flatten_empty(self):
        assert deep_flatten([]) == []


@pytest.mark.unit
class TestChunk:
    """Tests for chunk."""

    def test_chunk_splits_evenly(self):
        assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]

    def test_chunk_last_chunk_shorter(self):
        result = chunk([1, 2, 3, 4, 5], 2)
        assert result == [[1, 2], [3, 4], [5]]

    def test_chunk_size_larger_than_list(self):
        assert chunk([1, 2], 10) == [[1, 2]]

    def test_chunk_empty_list(self):
        assert chunk([], 3) == []

    def test_chunk_size_one(self):
        assert chunk([1, 2, 3], 1) == [[1], [2], [3]]

    def test_chunk_invalid_size_raises(self):
        with pytest.raises(ValueError, match="Chunk size must be positive"):
            chunk([1, 2, 3], 0)

    def test_chunk_negative_size_raises(self):
        with pytest.raises(ValueError, match="Chunk size must be positive"):
            chunk([1, 2, 3], -1)


@pytest.mark.unit
class TestToDict:
    """Tests for to_dict and to_dict_with_transform."""

    def test_to_dict_creates_dict_with_key_func(self):
        items = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        result = to_dict(items, lambda x: x["id"])
        assert result[1]["name"] == "a"
        assert result[2]["name"] == "b"

    def test_to_dict_empty_iterable(self):
        assert to_dict([], lambda x: x) == {}

    def test_to_dict_with_transform(self):
        items = [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]
        result = to_dict_with_transform(items, lambda x: x["id"], lambda x: x["name"])
        assert result == {1: "alpha", 2: "beta"}


@pytest.mark.unit
class TestToContainerTypes:
    """Tests for to_list, to_set, to_tuple."""

    def test_to_list_from_generator(self):
        gen = (x for x in range(3))
        assert to_list(gen) == [0, 1, 2]

    def test_to_set_removes_duplicates(self):
        assert to_set([1, 2, 2, 3]) == {1, 2, 3}

    def test_to_tuple_from_list(self):
        assert to_tuple([1, 2, 3]) == (1, 2, 3)


@pytest.mark.unit
class TestInvertDict:
    """Tests for invert_dict."""

    def test_invert_dict_swaps_keys_and_values(self):
        assert invert_dict({"a": 1, "b": 2}) == {1: "a", 2: "b"}

    def test_invert_dict_empty(self):
        assert invert_dict({}) == {}


@pytest.mark.unit
class TestMergeDicts:
    """Tests for merge_dicts and deep_merge_dicts."""

    def test_merge_dicts_later_overrides_earlier(self):
        result = merge_dicts({"a": 1, "b": 2}, {"b": 99, "c": 3})
        assert result == {"a": 1, "b": 99, "c": 3}

    def test_merge_dicts_three_dicts(self):
        result = merge_dicts({"a": 1}, {"b": 2}, {"c": 3})
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_merge_dicts_empty_dicts(self):
        assert merge_dicts({}, {}) == {}

    def test_deep_merge_dicts_merges_nested(self):
        d1 = {"a": {"x": 1, "y": 2}, "b": 3}
        d2 = {"a": {"y": 20, "z": 30}}
        result = deep_merge_dicts(d1, d2)
        assert result == {"a": {"x": 1, "y": 20, "z": 30}, "b": 3}

    def test_deep_merge_dicts_does_not_mutate_inputs(self):
        d1 = {"a": {"x": 1}}
        d2 = {"a": {"x": 99}}
        deep_merge_dicts(d1, d2)
        assert d1["a"]["x"] == 1

    def test_deep_merge_dicts_replaces_non_dict(self):
        d1 = {"a": [1, 2, 3]}
        d2 = {"a": [4, 5]}
        result = deep_merge_dicts(d1, d2)
        assert result["a"] == [4, 5]

    def test_deep_merge_dicts_adds_new_keys(self):
        result = deep_merge_dicts({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

"""Tests for common/collections/filtering.py utilities."""

import pytest

from orb.infrastructure.utilities.common.collections.filtering import (
    contains,
    contains_all,
    contains_any,
    distinct,
    distinct_by,
    filter_by,
    find,
    find_duplicates,
    find_index,
    has_duplicates,
    remove_duplicates,
)


@pytest.mark.unit
class TestFilterBy:
    """Tests for filter_by."""

    def test_filter_by_keeps_matching_elements(self):
        result = filter_by([1, 2, 3, 4, 5], lambda x: x > 3)
        assert result == [4, 5]

    def test_filter_by_empty_collection(self):
        assert filter_by([], lambda x: True) == []

    def test_filter_by_none_match_returns_empty(self):
        assert filter_by([1, 2, 3], lambda x: x > 100) == []

    def test_filter_by_all_match(self):
        assert filter_by([1, 2, 3], lambda x: x > 0) == [1, 2, 3]

    def test_filter_by_with_strings(self):
        result = filter_by(["apple", "banana", "avocado"], lambda s: s.startswith("a"))
        assert result == ["apple", "avocado"]


@pytest.mark.unit
class TestFind:
    """Tests for find."""

    def test_find_returns_first_match(self):
        result = find([1, 2, 3, 4], lambda x: x > 2)
        assert result == 3

    def test_find_returns_none_when_no_match(self):
        assert find([1, 2, 3], lambda x: x > 10) is None

    def test_find_empty_collection_returns_none(self):
        assert find([], lambda x: True) is None

    def test_find_with_string_predicate(self):
        result = find(["cat", "dog", "cow"], lambda s: s.startswith("d"))
        assert result == "dog"


@pytest.mark.unit
class TestFindIndex:
    """Tests for find_index."""

    def test_find_index_returns_correct_index(self):
        assert find_index([10, 20, 30, 40], lambda x: x == 30) == 2

    def test_find_index_first_match(self):
        assert find_index([1, 2, 2, 3], lambda x: x == 2) == 1

    def test_find_index_no_match_returns_minus_one(self):
        assert find_index([1, 2, 3], lambda x: x > 100) == -1

    def test_find_index_empty_list_returns_minus_one(self):
        assert find_index([], lambda x: True) == -1


@pytest.mark.unit
class TestContains:
    """Tests for contains, contains_all, contains_any."""

    def test_contains_returns_true_for_existing_item(self):
        assert contains([1, 2, 3], 2) is True

    def test_contains_returns_false_for_missing_item(self):
        assert contains([1, 2, 3], 99) is False

    def test_contains_all_all_present(self):
        assert contains_all([1, 2, 3, 4], [2, 4]) is True

    def test_contains_all_some_missing(self):
        assert contains_all([1, 2, 3], [2, 99]) is False

    def test_contains_all_empty_items(self):
        assert contains_all([1, 2, 3], []) is True

    def test_contains_any_one_present(self):
        assert contains_any([1, 2, 3], [99, 2]) is True

    def test_contains_any_none_present(self):
        assert contains_any([1, 2, 3], [10, 20]) is False

    def test_contains_any_empty_items(self):
        assert contains_any([1, 2, 3], []) is False


@pytest.mark.unit
class TestDistinct:
    """Tests for distinct, distinct_by, remove_duplicates."""

    def test_distinct_removes_duplicates_preserves_order(self):
        assert distinct([3, 1, 2, 1, 3]) == [3, 1, 2]

    def test_distinct_empty_input(self):
        assert distinct([]) == []

    def test_distinct_no_duplicates(self):
        assert distinct([1, 2, 3]) == [1, 2, 3]

    def test_distinct_by_key_function(self):
        data = [{"id": 1, "val": "a"}, {"id": 2, "val": "b"}, {"id": 1, "val": "c"}]
        result = distinct_by(data, lambda d: d["id"])
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2

    def test_remove_duplicates_is_alias_for_distinct(self):
        assert remove_duplicates([1, 1, 2, 3, 2]) == distinct([1, 1, 2, 3, 2])


@pytest.mark.unit
class TestFindDuplicates:
    """Tests for find_duplicates and has_duplicates."""

    def test_find_duplicates_returns_duplicate_values(self):
        result = find_duplicates([1, 2, 2, 3, 3, 4])
        assert set(result) == {2, 3}

    def test_find_duplicates_no_duplicates_returns_empty(self):
        assert find_duplicates([1, 2, 3]) == []

    def test_find_duplicates_empty_input(self):
        assert find_duplicates([]) == []

    def test_has_duplicates_true_when_duplicates_exist(self):
        assert has_duplicates([1, 2, 2, 3]) is True

    def test_has_duplicates_false_when_unique(self):
        assert has_duplicates([1, 2, 3]) is False

    def test_has_duplicates_empty_input(self):
        assert has_duplicates([]) is False

    def test_has_duplicates_single_element(self):
        assert has_duplicates([42]) is False

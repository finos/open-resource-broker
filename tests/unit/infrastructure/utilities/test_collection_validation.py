"""Tests for common/collections/validation.py utilities."""

from typing import Any

import pytest

from orb.infrastructure.utilities.common.collections.validation import (
    all_match,
    any_match,
    is_disjoint,
    is_empty,
    is_not_empty,
    is_sorted,
    is_subset,
    is_superset,
    none_match,
)


@pytest.mark.unit
class TestIsEmpty:
    """Tests for is_empty and is_not_empty."""

    def test_is_empty_list(self):
        assert is_empty([]) is True

    def test_is_empty_dict(self):
        assert is_empty({}) is True

    def test_is_empty_set(self):
        assert is_empty(set()) is True

    def test_is_empty_tuple(self):
        assert is_empty(()) is True

    def test_is_empty_string(self):
        assert is_empty("") is True

    def test_is_empty_non_empty_list(self):
        assert is_empty([1]) is False

    def test_is_not_empty_list(self):
        assert is_not_empty([1, 2]) is True

    def test_is_not_empty_empty_list(self):
        assert is_not_empty([]) is False

    def test_is_not_empty_string(self):
        assert is_not_empty("hello") is True


@pytest.mark.unit
class TestIsSorted:
    """Tests for is_sorted."""

    def test_is_sorted_ascending(self):
        data: list[Any] = [1, 2, 3, 4]
        assert is_sorted(data) is True

    def test_is_sorted_descending_with_flag(self):
        data: list[Any] = [4, 3, 2, 1]
        assert is_sorted(data, reverse=True) is True

    def test_is_sorted_not_sorted(self):
        data: list[Any] = [1, 3, 2]
        assert is_sorted(data) is False

    def test_is_sorted_single_element(self):
        data: list[Any] = [42]
        assert is_sorted(data) is True

    def test_is_sorted_empty(self):
        data: list[Any] = []
        assert is_sorted(data) is True

    def test_is_sorted_with_equal_elements(self):
        data: list[Any] = [1, 1, 2, 2]
        assert is_sorted(data) is True

    def test_is_sorted_descending_not_ascending(self):
        data: list[Any] = [3, 2, 1]
        assert is_sorted(data, reverse=False) is False


@pytest.mark.unit
class TestMatchPredicates:
    """Tests for all_match, any_match, none_match."""

    def test_all_match_all_positive(self):
        assert all_match([2, 4, 6], lambda x: x % 2 == 0) is True

    def test_all_match_one_fails(self):
        assert all_match([2, 4, 5], lambda x: x % 2 == 0) is False

    def test_all_match_empty_returns_true(self):
        assert all_match([], lambda x: False) is True

    def test_any_match_one_passes(self):
        assert any_match([1, 2, 3], lambda x: x > 2) is True

    def test_any_match_none_pass(self):
        assert any_match([1, 2, 3], lambda x: x > 100) is False

    def test_any_match_empty_returns_false(self):
        assert any_match([], lambda x: True) is False

    def test_none_match_none_pass(self):
        assert none_match([1, 2, 3], lambda x: x > 100) is True

    def test_none_match_one_passes(self):
        assert none_match([1, 2, 3], lambda x: x == 2) is False

    def test_none_match_empty_returns_true(self):
        assert none_match([], lambda x: True) is True


@pytest.mark.unit
class TestSetOperations:
    """Tests for is_subset, is_superset, is_disjoint."""

    def test_is_subset_true(self):
        assert is_subset({1, 2}, {1, 2, 3}) is True

    def test_is_subset_false(self):
        assert is_subset({1, 4}, {1, 2, 3}) is False

    def test_is_subset_equal_sets(self):
        assert is_subset({1, 2}, {1, 2}) is True

    def test_is_superset_true(self):
        assert is_superset({1, 2, 3}, {1, 2}) is True

    def test_is_superset_false(self):
        assert is_superset({1, 2}, {1, 2, 3}) is False

    def test_is_disjoint_true(self):
        assert is_disjoint({1, 2}, {3, 4}) is True

    def test_is_disjoint_false(self):
        assert is_disjoint({1, 2}, {2, 3}) is False

    def test_is_disjoint_empty_sets(self):
        assert is_disjoint(set(), {1, 2}) is True

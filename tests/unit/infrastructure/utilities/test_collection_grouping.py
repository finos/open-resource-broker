"""Tests for common/collections/grouping.py utilities."""

import pytest

from orb.infrastructure.utilities.common.collections.grouping import (
    count_by,
    count_occurrences,
    frequency_map,
    group_by,
    least_common,
    most_common,
    partition,
)


@pytest.mark.unit
class TestGroupBy:
    """Tests for group_by."""

    def test_group_by_partitions_by_key(self):
        data = [1, 2, 3, 4, 5, 6]
        result = group_by(data, lambda x: "even" if x % 2 == 0 else "odd")
        assert sorted(result["even"]) == [2, 4, 6]
        assert sorted(result["odd"]) == [1, 3, 5]

    def test_group_by_empty_input(self):
        assert group_by([], lambda x: x) == {}

    def test_group_by_single_group(self):
        result = group_by([1, 2, 3], lambda x: "all")
        assert result == {"all": [1, 2, 3]}

    def test_group_by_with_dicts(self):
        items = [{"type": "a"}, {"type": "b"}, {"type": "a"}]
        result = group_by(items, lambda x: x["type"])
        assert len(result["a"]) == 2
        assert len(result["b"]) == 1


@pytest.mark.unit
class TestPartition:
    """Tests for partition."""

    def test_partition_splits_correctly(self):
        evens, odds = partition([1, 2, 3, 4, 5], lambda x: x % 2 == 0)
        assert evens == [2, 4]
        assert odds == [1, 3, 5]

    def test_partition_all_match(self):
        match, no_match = partition([2, 4, 6], lambda x: x % 2 == 0)
        assert match == [2, 4, 6]
        assert no_match == []

    def test_partition_none_match(self):
        match, no_match = partition([1, 3, 5], lambda x: x % 2 == 0)
        assert match == []
        assert no_match == [1, 3, 5]

    def test_partition_empty_input(self):
        match, no_match = partition([], lambda x: True)
        assert match == []
        assert no_match == []


@pytest.mark.unit
class TestCountBy:
    """Tests for count_by."""

    def test_count_by_counts_by_key(self):
        result = count_by(["a", "b", "a", "c", "b", "b"], lambda x: x)
        assert result == {"a": 2, "b": 3, "c": 1}

    def test_count_by_empty_input(self):
        assert count_by([], lambda x: x) == {}

    def test_count_by_with_transform(self):
        result = count_by([1, 2, 3, 4, 5], lambda x: "even" if x % 2 == 0 else "odd")
        assert result["even"] == 2
        assert result["odd"] == 3


@pytest.mark.unit
class TestCountOccurrences:
    """Tests for count_occurrences."""

    def test_count_occurrences_counts_each_element(self):
        result = count_occurrences(["x", "y", "x", "z", "x"])
        assert result == {"x": 3, "y": 1, "z": 1}

    def test_count_occurrences_empty(self):
        assert count_occurrences([]) == {}

    def test_count_occurrences_all_unique(self):
        result = count_occurrences([1, 2, 3])
        assert all(v == 1 for v in result.values())


@pytest.mark.unit
class TestFrequencyMap:
    """Tests for frequency_map."""

    def test_frequency_map_sums_to_one(self):
        result = frequency_map(["a", "b", "a"])
        total = sum(result.values())
        assert abs(total - 1.0) < 1e-9

    def test_frequency_map_correct_ratio(self):
        result = frequency_map(["a", "a", "b"])
        assert abs(result["a"] - 2 / 3) < 1e-9
        assert abs(result["b"] - 1 / 3) < 1e-9

    def test_frequency_map_empty_returns_empty(self):
        assert frequency_map([]) == {}


@pytest.mark.unit
class TestMostLeastCommon:
    """Tests for most_common and least_common."""

    def test_most_common_returns_sorted_descending(self):
        result = most_common(["a", "b", "a", "a", "b"])
        assert result[0] == ("a", 3)
        assert result[1] == ("b", 2)

    def test_most_common_limited_n(self):
        result = most_common([1, 2, 2, 3, 3, 3], n=2)
        assert len(result) == 2
        assert result[0][0] == 3

    def test_most_common_empty(self):
        assert most_common([]) == []

    def test_least_common_returns_sorted_ascending(self):
        result = least_common(["a", "b", "a", "a", "b", "c"])
        # "c" appears once — should be first
        assert result[0] == ("c", 1)

    def test_least_common_limited_n(self):
        result = least_common([1, 1, 1, 2, 2, 3], n=1)
        assert len(result) == 1
        assert result[0] == (3, 1)

    def test_least_common_empty(self):
        assert least_common([]) == []

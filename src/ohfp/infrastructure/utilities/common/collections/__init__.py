"""Collection utility functions organized by responsibility."""

from typing import Any, Callable

# Import specific functions from submodules
from infrastructure.utilities.common.collections.filtering import (
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
from infrastructure.utilities.common.collections.grouping import (
    count_by,
    count_occurrences,
    frequency_map,
    group_by,
    least_common,
    most_common,
    partition,
)
from infrastructure.utilities.common.collections.transforming import (
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
from infrastructure.utilities.common.collections.validation import (
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


# Utility aliases for backward compatibility
def filter_dict(
    dictionary: dict[Any, Any], predicate: Callable[[Any, Any], bool]
) -> dict[Any, Any]:
    """Filter dictionary by predicate - alias for compatibility."""
    return {k: v for k, v in dictionary.items() if predicate(k, v)}


def transform_list(collection: list[Any], transform_func: Callable[[Any], Any]) -> list[Any]:
    """Transform list elements - alias for compatibility."""
    return [transform_func(item) for item in collection]


def validate_collection(collection: list[Any], validator_func: Callable[[Any], bool]) -> bool:
    """Validate collection elements - alias for compatibility."""
    return all_match(collection, validator_func)


# Export commonly used functions
__all__: list[str] = [
    "all_match",
    "any_match",
    "chunk",
    "contains",
    "contains_all",
    "contains_any",
    "count_by",
    "count_occurrences",
    "deep_flatten",
    "deep_merge_dicts",
    "distinct",
    "distinct_by",
    # Filtering functions
    "filter_by",
    "find",
    "find_duplicates",
    "find_index",
    "flatten",
    "frequency_map",
    # Grouping functions
    "group_by",
    "has_duplicates",
    "invert_dict",
    "is_disjoint",
    # Validation functions
    "is_empty",
    "is_not_empty",
    "is_sorted",
    "is_subset",
    "is_superset",
    "least_common",
    "map_keys",
    # Transformation functions
    "map_values",
    "merge_dicts",
    "most_common",
    "none_match",
    "partition",
    "remove_duplicates",
    "to_dict",
    "to_dict_with_transform",
    "to_list",
    "to_set",
    "to_tuple",
]

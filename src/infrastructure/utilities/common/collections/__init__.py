"""Collection utility functions organized by responsibility."""

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
def filter_dict(dictionary, predicate):
    """Filter dictionary by predicate - alias for compatibility."""
    return {k: v for k, v in dictionary.items() if predicate(k, v)}


def transform_list(collection, transform_func):
    """Transform list elements - alias for compatibility."""
    return [transform_func(item) for item in collection]


def validate_collection(collection, validator_func):
    """Validate collection elements - alias for compatibility."""
    return all_match(collection, validator_func)


# Export commonly used functions
__all__ = [
    # Validation functions
    "is_empty",
    "is_not_empty",
    "is_sorted",
    "all_match",
    "any_match",
    "none_match",
    "is_subset",
    "is_superset",
    "is_disjoint",
    # Filtering functions
    "filter_by",
    "find",
    "find_index",
    "contains",
    "contains_all",
    "contains_any",
    "distinct",
    "distinct_by",
    "remove_duplicates",
    "find_duplicates",
    "has_duplicates",
    # Transformation functions
    "map_values",
    "map_keys",
    "flatten",
    "deep_flatten",
    "chunk",
    "to_dict",
    "to_dict_with_transform",
    "to_list",
    "to_set",
    "to_tuple",
    "invert_dict",
    "merge_dicts",
    "deep_merge_dicts",
    # Grouping functions
    "group_by",
    "partition",
    "count_by",
    "count_occurrences",
    "frequency_map",
    "most_common",
    "least_common",
]

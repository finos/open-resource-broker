"""Collection transformation utility functions."""

import copy
from typing import Any, Callable, Dict, Iterable, List, Set, Tuple, TypeVar

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")


def map_values(collection: Dict[K, V], transform_func: Callable[[V], Any]) -> Dict[K, Any]:
    """
    Transform dictionary values.

    Args:
        collection: Dictionary to transform
        transform_func: Function to transform values

    Returns:
        Dictionary with transformed values
    """
    return {key: transform_func(value) for key, value in collection.items()}


def map_keys(collection: Dict[K, V], transform_func: Callable[[K], Any]) -> Dict[Any, V]:
    """
    Transform dictionary keys.

    Args:
        collection: Dictionary to transform
        transform_func: Function to transform keys

    Returns:
        Dictionary with transformed keys
    """
    return {transform_func(key): value for key, value in collection.items()}


def flatten(collection: List[List[T]]) -> List[T]:
    """
    Flatten a list of lists.

    Args:
        collection: List of lists to flatten

    Returns:
        Flattened list
    """
    result = []
    for sublist in collection:
        result.extend(sublist)
    return result


def deep_flatten(collection: List[Any]) -> List[Any]:
    """
    Recursively flatten nested lists.

    Args:
        collection: Nested list structure

    Returns:
        Completely flattened list
    """
    result = []
    for item in collection:
        if isinstance(item, list):
            result.extend(deep_flatten(item))
        else:
            result.append(item)
    return result


def chunk(collection: List[T], size: int) -> List[List[T]]:
    """
    Split list into chunks of specified size.

    Args:
        collection: List to chunk
        size: Size of each chunk

    Returns:
        List of chunks
    """
    if size <= 0:
        raise ValueError("Chunk size must be positive") from e

    return [collection[i : i + size] for i in range(0, len(collection), size)]


def to_dict(collection: Iterable[T], key_func: Callable[[T], K]) -> Dict[K, T]:
    """
    Convert collection to dictionary using key function.

    Args:
        collection: Collection to convert
        key_func: Function to extract key from each item

    Returns:
        Dictionary with items keyed by key_func result
    """
    return {key_func(item): item for item in collection}


def to_dict_with_transform(
    collection: Iterable[T], key_func: Callable[[T], K], value_func: Callable[[T], V]
) -> Dict[K, V]:
    """
    Convert collection to dictionary with key and value transformations.

    Args:
        collection: Collection to convert
        key_func: Function to extract key from each item
        value_func: Function to transform each item to value

    Returns:
        Dictionary with transformed keys and values
    """
    return {key_func(item): value_func(item) for item in collection}


def to_list(collection: Iterable[T]) -> List[T]:
    """
    Convert iterable to list.

    Args:
        collection: Iterable to convert

    Returns:
        List containing all items
    """
    return list(collection)


def to_set(collection: Iterable[T]) -> Set[T]:
    """
    Convert iterable to set.

    Args:
        collection: Iterable to convert

    Returns:
        Set containing unique items
    """
    return set(collection)


def to_tuple(collection: Iterable[T]) -> Tuple[T, ...]:
    """
    Convert iterable to tuple.

    Args:
        collection: Iterable to convert

    Returns:
        Tuple containing all items
    """
    return tuple(collection)


def invert_dict(collection: Dict[K, V]) -> Dict[V, K]:
    """
    Invert dictionary (swap keys and values).

    Args:
        collection: Dictionary to invert

    Returns:
        Dictionary with keys and values swapped
    """
    return {value: key for key, value in collection.items()}


def merge_dicts(*dicts: Dict[K, V]) -> Dict[K, V]:
    """
    Merge multiple dictionaries.

    Args:
        dicts: Dictionaries to merge

    Returns:
        Merged dictionary (later values override earlier ones)
    """
    result = {}
    for d in dicts:
        result.update(d)
    return result


def deep_merge_dicts(dict1: Dict[K, Any], dict2: Dict[K, Any]) -> Dict[K, Any]:
    """
    Deep merge two dictionaries.

    Args:
        dict1: First dictionary
        dict2: Second dictionary

    Returns:
        Deep merged dictionary
    """
    result = copy.deepcopy(dict1)

    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge_dicts(result[key], value)
        else:
            result[key] = copy.deepcopy(value)

    return result

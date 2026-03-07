"""Collection filtering utility functions."""

from collections.abc import Iterable
from typing import Callable, Optional, TypeVar

T = TypeVar("T")
K = TypeVar("K")


def filter_by(collection: Iterable[T], predicate: Callable[[T], bool]) -> list[T]:
    """
    Filter collection by predicate.

    Args:
        collection: Collection to filter
        predicate: Function to test each element

    Returns:
        List of elements that match predicate
    """
    return [item for item in collection if predicate(item)]


def find(collection: Iterable[T], predicate: Callable[[T], bool]) -> Optional[T]:
    """
    Find first element matching predicate.

    Args:
        collection: Collection to search
        predicate: Function to test each element

    Returns:
        First matching element or None
    """
    for item in collection:
        if predicate(item):
            return item
    return None


def find_index(collection: list[T], predicate: Callable[[T], bool]) -> int:
    """
    Find index of first element matching predicate.

    Args:
        collection: List to search
        predicate: Function to test each element

    Returns:
        Index of first matching element, -1 if not found
    """
    for i, item in enumerate(collection):
        if predicate(item):
            return i
    return -1


def contains(collection: Iterable[T], item: T) -> bool:
    """
    Check if collection contains item.

    Args:
        collection: Collection to check
        item: Item to find

    Returns:
        True if item is in collection
    """
    return item in collection


def contains_all(collection: Iterable[T], items: Iterable[T]) -> bool:
    """
    Check if collection contains all items.

    Args:
        collection: Collection to check
        items: Items to find

    Returns:
        True if all items are in collection
    """
    collection_set = set(collection)
    return all(item in collection_set for item in items)


def contains_any(collection: Iterable[T], items: Iterable[T]) -> bool:
    """
    Check if collection contains any of the items.

    Args:
        collection: Collection to check
        items: Items to find

    Returns:
        True if any item is in collection
    """
    collection_set = set(collection)
    return any(item in collection_set for item in items)


def distinct(collection: Iterable[T]) -> list[T]:
    """
    Get distinct elements from collection.

    Args:
        collection: Collection to process

    Returns:
        List of unique elements
    """
    seen = set()
    result = []
    for item in collection:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def distinct_by(collection: Iterable[T], key_func: Callable[[T], K]) -> list[T]:
    """
    Get distinct elements by key function.

    Args:
        collection: Collection to process
        key_func: Function to extract key for comparison

    Returns:
        List of elements unique by key
    """
    seen = set()
    result = []
    for item in collection:
        key = key_func(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def remove_duplicates(collection: list[T]) -> list[T]:
    """
    Remove duplicates from list while preserving order.

    Args:
        collection: List to process

    Returns:
        List without duplicates
    """
    return distinct(collection)


def find_duplicates(collection: Iterable[T]) -> list[T]:
    """
    Find duplicate elements in collection.

    Args:
        collection: Collection to check

    Returns:
        List of duplicate elements
    """
    seen = set()
    duplicates = set()
    for item in collection:
        if item in seen:
            duplicates.add(item)
        else:
            seen.add(item)
    return list(duplicates)


def has_duplicates(collection: Iterable[T]) -> bool:
    """
    Check if collection has duplicate elements.

    Args:
        collection: Collection to check

    Returns:
        True if collection has duplicates
    """
    seen = set()
    for item in collection:
        if item in seen:
            return True
        seen.add(item)
    return False

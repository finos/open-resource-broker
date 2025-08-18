"""Collection grouping utility functions."""

from collections import Counter, defaultdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, TypeVar

T = TypeVar("T")
K = TypeVar("K")


def group_by(collection: Iterable[T], key_func: Callable[[T], K]) -> Dict[K, List[T]]:
    """
    Group collection elements by key function.

    Args:
        collection: Collection to group
        key_func: Function to extract grouping key

    Returns:
        Dictionary mapping keys to lists of items
    """
    groups = defaultdict(list)
    for item in collection:
        key = key_func(item)
        groups[key].append(item)
    return dict(groups)


def partition(collection: Iterable[T], predicate: Callable[[T], bool]) -> Tuple[List[T], List[T]]:
    """
    Partition collection into two lists based on predicate.

    Args:
        collection: Collection to partition
        predicate: Function to test each element

    Returns:
        Tuple of (matching elements, non-matching elements)
    """
    true_items = []
    false_items = []

    for item in collection:
        if predicate(item):
            true_items.append(item)
        else:
            false_items.append(item)

    return true_items, false_items


def count_by(collection: Iterable[T], key_func: Callable[[T], K]) -> Dict[K, int]:
    """
    Count occurrences by key function.

    Args:
        collection: Collection to count
        key_func: Function to extract counting key

    Returns:
        Dictionary mapping keys to counts
    """
    counts: dict[Any, int] = defaultdict(int)
    for item in collection:
        key = key_func(item)
        counts[key] += 1
    return dict(counts)


def count_occurrences(collection: Iterable[T]) -> Dict[T, int]:
    """
    Count occurrences of each element.

    Args:
        collection: Collection to count

    Returns:
        Dictionary mapping elements to their counts
    """
    return dict(Counter(collection))


def frequency_map(collection: Iterable[T]) -> Dict[T, float]:
    """
    Get frequency map of elements (count / total).

    Args:
        collection: Collection to analyze

    Returns:
        Dictionary mapping elements to their frequencies
    """
    counts = count_occurrences(collection)
    total = sum(counts.values())

    if total == 0:
        return {}

    return {item: count / total for item, count in counts.items()}


def most_common(collection: Iterable[T], n: Optional[int] = None) -> List[Tuple[T, int]]:
    """
    Get most common elements.

    Args:
        collection: Collection to analyze
        n: Number of most common elements to return (None for all)

    Returns:
        List of (element, count) tuples sorted by count descending
    """
    counter = Counter(collection)
    return counter.most_common(n)


def least_common(collection: Iterable[T], n: Optional[int] = None) -> List[Tuple[T, int]]:
    """
    Get least common elements.

    Args:
        collection: Collection to analyze
        n: Number of least common elements to return (None for all)

    Returns:
        List of (element, count) tuples sorted by count ascending
    """
    counter = Counter(collection)
    all_common = counter.most_common()
    all_common.reverse()  # Reverse to get least common first

    if n is None:
        return all_common
    else:
        return all_common[:n]

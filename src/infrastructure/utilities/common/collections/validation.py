"""Collection validation utility functions."""

from typing import Callable, Dict, Iterable, List, Protocol, Set, Tuple, TypeVar, Union


class Comparable(Protocol):
    """Protocol for types that support comparison operators."""

    def __lt__(self, other: "Comparable") -> bool: ...
    def __le__(self, other: "Comparable") -> bool: ...
    def __gt__(self, other: "Comparable") -> bool: ...
    def __ge__(self, other: "Comparable") -> bool: ...


T = TypeVar("T")
C = TypeVar("C", bound=Comparable)


def is_empty(collection: Union[List, Dict, Set, Tuple, str]) -> bool:
    """
    Check if a collection is empty.

    Args:
        collection: Collection to check

    Returns:
        True if collection is empty, False otherwise
    """
    return len(collection) == 0


def is_not_empty(collection: Union[List, Dict, Set, Tuple, str]) -> bool:
    """
    Check if a collection is not empty.

    Args:
        collection: Collection to check

    Returns:
        True if collection is not empty, False otherwise
    """
    return not is_empty(collection)


def is_sorted(collection: List[C], reverse: bool = False) -> bool:
    """
    Check if list is sorted.

    Args:
        collection: List to check
        reverse: True for descending order

    Returns:
        True if list is sorted
    """
    if len(collection) <= 1:
        return True

    if reverse:
        return all(collection[i] >= collection[i + 1] for i in range(len(collection) - 1))
    else:
        return all(collection[i] <= collection[i + 1] for i in range(len(collection) - 1))


def all_match(collection: Iterable[T], predicate: Callable[[T], bool]) -> bool:
    """
    Check if all elements match predicate.

    Args:
        collection: Collection to check
        predicate: Function to test each element

    Returns:
        True if all elements match predicate
    """
    return all(predicate(item) for item in collection)


def any_match(collection: Iterable[T], predicate: Callable[[T], bool]) -> bool:
    """
    Check if any element matches predicate.

    Args:
        collection: Collection to check
        predicate: Function to test each element

    Returns:
        True if any element matches predicate
    """
    return any(predicate(item) for item in collection)


def none_match(collection: Iterable[T], predicate: Callable[[T], bool]) -> bool:
    """
    Check if no elements match predicate.

    Args:
        collection: Collection to check
        predicate: Function to test each element

    Returns:
        True if no elements match predicate
    """
    return not any_match(collection, predicate)


def is_subset(collection1: Set[T], collection2: Set[T]) -> bool:
    """
    Check if collection1 is subset of collection2.

    Args:
        collection1: First collection
        collection2: Second collection

    Returns:
        True if collection1 is subset of collection2
    """
    return collection1.issubset(collection2)


def is_superset(collection1: Set[T], collection2: Set[T]) -> bool:
    """
    Check if collection1 is superset of collection2.

    Args:
        collection1: First collection
        collection2: Second collection

    Returns:
        True if collection1 is superset of collection2
    """
    return collection1.issuperset(collection2)


def is_disjoint(collection1: Set[T], collection2: Set[T]) -> bool:
    """
    Check if collections are disjoint (no common elements).

    Args:
        collection1: First collection
        collection2: Second collection

    Returns:
        True if collections have no common elements
    """
    return collection1.isdisjoint(collection2)

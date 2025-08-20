"""Optimistic concurrency control utilities."""

import time
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, TypeVar

from domain.base.exceptions import ConcurrencyError
from infrastructure.logging.logger import get_logger

T = TypeVar("T")  # Entity type
R = TypeVar("R")  # Return type


class OptimisticConcurrencyControl:
    """Utilities for optimistic concurrency control."""

    def __init__(self, max_retries: int = 3, retry_delay: float = 0.1) -> None:
        """
        Initialize optimistic concurrency control.

        Args:
            max_retries: Maximum number of retries
            retry_delay: Delay between retries in seconds
        """
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.logger = get_logger(__name__)

    def retry_on_concurrency_error(self, func: Callable[..., R]) -> Callable[..., R]:
        """
        Retry a function on concurrency error.

        Args:
            func: Function to retry

        Returns:
            Decorated function
        """

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> R:
            """Wrap function."""
            retries = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except ConcurrencyError as e:
                    retries += 1
                    if retries > self.max_retries:
                        self.logger.warning(
                            "Maximum retries (%s) exceeded for concurrency error: %s",
                            self.max_retries,
                            e,
                        )
                        raise

                    self.logger.debug(
                        "Concurrency error detected, retrying (%s/%s): %s",
                        retries,
                        self.max_retries,
                        e,
                    )
                    time.sleep(self.retry_delay)

        return wrapper

    def check_version(
        self,
        entity: T,
        entity_id: str,
        version_map: Dict[str, int],
        entity_class_name: str,
    ) -> None:
        """
        Check entity version.

        Args:
            entity: Entity to check
            entity_id: Entity ID
            version_map: Version map
            entity_class_name: Entity class name

        Raises:
            ConcurrencyError: If entity version conflict
        """
        if entity_id in version_map and entity.version != version_map[entity_id]:
            raise ConcurrencyError(
                entity_class_name, entity_id, version_map[entity_id], entity.version
            )

    def increment_version(self, entity: T, entity_id: str, version_map: Dict[str, int]) -> None:
        """
        Increment entity version.

        Args:
            entity: Entity to increment version for
            entity_id: Entity ID
            version_map: Version map
        """
        version_map[entity_id] = entity.version + 1

    def batch_check_versions(
        self,
        entities: List[T],
        get_entity_id: Callable[[T], str],
        version_map: Dict[str, int],
        entity_class_name: str,
    ) -> None:
        """
        Check versions for a batch of entities.

        Args:
            entities: Entities to check
            get_entity_id: Function to get entity ID
            version_map: Version map
            entity_class_name: Entity class name

        Raises:
            ConcurrencyError: If any entity version conflict
        """
        for entity in entities:
            entity_id = get_entity_id(entity)
            self.check_version(entity, entity_id, version_map, entity_class_name)

    def batch_increment_versions(
        self,
        entities: List[T],
        get_entity_id: Callable[[T], str],
        version_map: Dict[str, int],
    ) -> None:
        """
        Increment versions for a batch of entities.

        Args:
            entities: Entities to increment versions for
            get_entity_id: Function to get entity ID
            version_map: Version map
        """
        for entity in entities:
            entity_id = get_entity_id(entity)
            self.increment_version(entity, entity_id, version_map)


# Global instance
_optimistic_concurrency_control: Optional[OptimisticConcurrencyControl] = None


def get_optimistic_concurrency_control() -> OptimisticConcurrencyControl:
    """
    Get the global optimistic concurrency control instance.

    Returns:
        Global optimistic concurrency control instance
    """
    global _optimistic_concurrency_control
    if _optimistic_concurrency_control is None:
        _optimistic_concurrency_control = OptimisticConcurrencyControl()
    return _optimistic_concurrency_control

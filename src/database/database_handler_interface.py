from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List
from src.helpers.logger import setup_logging

logger = setup_logging()


class BaseDatabaseHandler(ABC):
    """
    Abstract base class defining the interface for database backends.
    """

    @abstractmethod
    def insert(self, table: str, key: str, value: Dict[str, Any]) -> None:
        """Insert a new item into the specified table."""
        pass

    @abstractmethod
    def get(self, table: str, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve an item from the specified table by its key."""
        pass

    @abstractmethod
    def update(self, table: str, key: str, value: Dict[str, Any]) -> None:
        """Update an existing item in the specified table."""
        pass

    @abstractmethod
    def delete(self, table: str, key: str) -> None:
        """Delete an item from the specified table by its key."""
        pass

    @abstractmethod
    def query(self, table: str, conditions: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Query items from the specified table based on conditions."""
        pass

    @abstractmethod
    def scan(self, table: str) -> List[Dict[str, Any]]:
        """Scan all items from the specified table."""
        pass

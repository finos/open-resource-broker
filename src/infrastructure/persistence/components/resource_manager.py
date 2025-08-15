"""Base storage resource manager interface for persistence components."""

from abc import ABC, abstractmethod
from typing import Any, Dict

from infrastructure.logging.logger import get_logger


class StorageResourceManager(ABC):
    """
    Base interface for storage resource managers.

    Provides common interface for managing different types of storage resources
    like database connections, AWS clients, file handles, etc.

    This is distinct from the domain ResourceManager which handles cloud resources.
    This class handles storage/persistence infrastructure resources.
    """

    def __init__(self):
        """Initialize the instance."""
        self.logger = get_logger(__name__)
        self._initialized = False

    @abstractmethod
    def initialize(self) -> None:
        """
        Initialize the storage resource manager.

        Should set up connections, clients, or other resources needed
        for the specific storage technology.
        """

    @abstractmethod
    def cleanup(self) -> None:
        """
        Clean up resources managed by this manager.

        Should close connections, release file handles, etc.
        """

    @abstractmethod
    def get_resource(self, resource_name: str) -> Any:
        """
        Get a managed resource by name.

        Args:
            resource_name: Name/identifier of the resource

        Returns:
            The requested resource (connection, client, etc.)
        """

    @abstractmethod
    def is_healthy(self) -> bool:
        """
        Check if the resource manager and its resources are healthy.

        Returns:
            True if all managed resources are healthy, False otherwise
        """

    def get_status(self) -> Dict[str, Any]:
        """
        Get status information about managed resources.

        Returns:
            Dictionary containing status information
        """
        return {
            "initialized": self._initialized,
            "healthy": self.is_healthy(),
            "manager_type": self.__class__.__name__,
        }


class QueryManager(ABC):
    """
    Base interface for query managers.

    Provides common interface for building and executing queries
    across different storage technologies.
    """

    def __init__(self):
        self.logger = get_logger(__name__)

    @abstractmethod
    def build_query(self, query_spec: Dict[str, Any]) -> str:
        """
        Build a query string from specification.

        Args:
            query_spec: Dictionary containing query specification

        Returns:
            Query string in the appropriate format for the storage technology
        """

    @abstractmethod
    def execute_query(self, query: str, parameters: Dict[str, Any] = None) -> Any:
        """
        Execute a query with optional parameters.

        Args:
            query: Query string to execute
            parameters: Optional parameters for the query

        Returns:
            Query results in appropriate format
        """

    @abstractmethod
    def validate_query(self, query: str) -> bool:
        """
        Validate a query string.

        Args:
            query: Query string to validate

        Returns:
            True if query is valid, False otherwise
        """


class DataConverter(ABC):
    """
    Base interface for data converters.

    Provides common interface for converting data between different formats
    used in storage operations.
    """

    @abstractmethod
    def to_storage_format(self, data: Any) -> Any:
        """
        Convert data to storage format.

        Args:
            data: Data in domain/application format

        Returns:
            Data in storage-specific format
        """

    @abstractmethod
    def from_storage_format(self, data: Any) -> Any:
        """
        Convert data from storage format.

        Args:
            data: Data in storage-specific format

        Returns:
            Data in domain/application format
        """


# Backward compatibility alias
ResourceManager = StorageResourceManager

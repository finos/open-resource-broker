"""Persistence layer exceptions."""

from typing import Any, Dict, Optional

from domain.base.exceptions import InfrastructureError


class PersistenceError(InfrastructureError):
    """Base exception for persistence errors."""

    def __init__(self, message: str, cause: Optional[Exception] = None) -> None:
        """
        Initialize persistence error.

        Args:
            message: Error message
            cause: Cause of the error
        """
        super().__init__("Persistence", message)
        self.cause = cause

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert exception to dictionary.

        Returns:
            Dictionary representation of exception
        """
        result = super().to_dict()
        if self.cause:
            result["cause"] = str(self.cause)
        return result


class ConnectionError(PersistenceError):
    """Exception for connection errors."""

    def __init__(self, message: str, connection_details: Optional[Dict[str, Any]] = None) -> None:
        """
        Initialize connection error.

        Args:
            message: Error message
            connection_details: Connection details
        """
        super().__init__(message)
        self.connection_details = connection_details or {}

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert exception to dictionary.

        Returns:
            Dictionary representation of exception
        """
        result = super().to_dict()
        # Remove sensitive information from connection details
        if self.connection_details:
            safe_details = self.connection_details.copy()
            for key in ["password", "secret", "key", "token"]:
                if key in safe_details:
                    safe_details[key] = "***"
            result["connection_details"] = safe_details
        return result


class DataIntegrityError(PersistenceError):
    """Exception for data integrity errors."""

    def __init__(
        self,
        message: str,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> None:
        """
        Initialize data integrity error.

        Args:
            message: Error message
            entity_type: Entity type
            entity_id: Entity ID
        """
        super().__init__(message)
        self.entity_type = entity_type
        self.entity_id = entity_id

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert exception to dictionary.

        Returns:
            Dictionary representation of exception
        """
        result = super().to_dict()
        if self.entity_type:
            result["entity_type"] = self.entity_type
        if self.entity_id:
            result["entity_id"] = self.entity_id
        return result


class StorageError(PersistenceError):
    """Exception for storage errors."""

    def __init__(self, message: str, storage_type: Optional[str] = None) -> None:
        """
        Initialize storage error.

        Args:
            message: Error message
            storage_type: Storage type
        """
        super().__init__(message)
        self.storage_type = storage_type

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert exception to dictionary.

        Returns:
            Dictionary representation of exception
        """
        result = super().to_dict()
        if self.storage_type:
            result["storage_type"] = self.storage_type
        return result


class TransactionError(PersistenceError):
    """Exception for transaction errors."""

    def __init__(self, message: str, transaction_id: Optional[str] = None) -> None:
        """
        Initialize transaction error.

        Args:
            message: Error message
            transaction_id: Transaction ID
        """
        super().__init__(message)
        self.transaction_id = transaction_id

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert exception to dictionary.

        Returns:
            Dictionary representation of exception
        """
        result = super().to_dict()
        if self.transaction_id:
            result["transaction_id"] = self.transaction_id
        return result

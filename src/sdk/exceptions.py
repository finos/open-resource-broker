"""
SDK-specific exceptions following existing error handling patterns.

Follows the same patterns as domain and infrastructure exceptions
for consistency and structured error handling throughout the system.
"""

from typing import Any, Dict, Optional


class SDKError(Exception):
    """Base exception for all SDK-related errors."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        """Initialize the instance."""
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} (Details: {self.details})"
        return self.message


class ConfigurationError(SDKError):
    """Raised when SDK configuration is invalid or missing."""

    def __init__(
        self,
        message: str,
        config_key: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, details)
        self.config_key = config_key


class ProviderError(SDKError):
    """Raised when provider initialization or operations fail."""

    def __init__(
        self,
        message: str,
        provider: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, details)
        self.provider = provider


class HandlerDiscoveryError(SDKError):
    """Raised when handler discovery fails."""

    def __init__(
        self,
        message: str,
        handler_type: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, details)
        self.handler_type = handler_type


class MethodExecutionError(SDKError):
    """Raised when SDK method execution fails."""

    def __init__(
        self,
        message: str,
        method_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, details)
        self.method_name = method_name

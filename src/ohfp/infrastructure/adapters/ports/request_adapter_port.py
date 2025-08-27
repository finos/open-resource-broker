"""
Request Adapter Port

This module defines the interface for request adapters.
"""

from abc import ABC, abstractmethod
from typing import Any

from domain.request.aggregate import Request


class RequestAdapterPort(ABC):
    """Interface for request adapters."""

    @abstractmethod
    def get_request_status(self, request: Request) -> dict[str, Any]:
        """
        Get provider-specific status for request.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with status information
        """

    @abstractmethod
    def cancel_fleet_request(self, request: Request) -> dict[str, Any]:
        """
        Cancel fleet request.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with cancellation results
        """

    @abstractmethod
    def terminate_instances(self, instance_ids: list[str]) -> dict[str, Any]:
        """
        Terminate instances.

        Args:
            instance_ids: List of instance IDs to terminate

        Returns:
            Dictionary with termination results
        """

"""
Request Adapter Port

This module defines the interface for request adapters.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from domain.request.aggregate import Request


class RequestAdapterPort(ABC):
    """Interface for request adapters."""

    @abstractmethod
    def get_request_status(self, request: Request) -> Dict[str, Any]:
        """
        Get provider-specific status for request.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with status information
        """

    @abstractmethod
    def cancel_fleet_request(self, request: Request) -> Dict[str, Any]:
        """
        Cancel fleet request.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with cancellation results
        """

    @abstractmethod
    def terminate_instances(self, instance_ids: List[str]) -> Dict[str, Any]:
        """
        Terminate instances.

        Args:
            instance_ids: List of instance IDs to terminate

        Returns:
            Dictionary with termination results
        """

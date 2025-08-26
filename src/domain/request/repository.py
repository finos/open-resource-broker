"""Request repository interface - contract for request data access."""

from abc import abstractmethod
from datetime import datetime
from typing import Optional

from domain.base.domain_interfaces import AggregateRepository

from .aggregate import Request, RequestStatus, RequestType


class RequestRepository(AggregateRepository[Request]):
    """Repository interface for request aggregates."""

    @abstractmethod
    def find_by_request_id(self, request_id: str) -> Optional[Request]:
        """Find request by request ID."""

    @abstractmethod
    def find_by_status(self, status: RequestStatus) -> list[Request]:
        """Find requests by status."""

    @abstractmethod
    def find_by_type(self, request_type: RequestType) -> list[Request]:
        """Find requests by type."""

    @abstractmethod
    def find_pending_requests(self) -> list[Request]:
        """Find all pending requests."""

    @abstractmethod
    def find_active_requests(self) -> list[Request]:
        """Find all active (non-completed/failed) requests."""

    @abstractmethod
    def find_by_date_range(self, start_date: datetime, end_date: datetime) -> list[Request]:
        """Find requests within date range."""

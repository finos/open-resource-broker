"""Request repository interface - contract for request data access."""

from abc import abstractmethod
from datetime import datetime
from typing import Optional

from orb.domain.base.domain_interfaces import AggregateRepository

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
    def find_by_ids(self, request_ids: list[str]) -> list[Request]:
        """Find requests by multiple request IDs."""

    @abstractmethod
    def find_active_requests(self) -> list[Request]:
        """Find all active (non-completed/failed) requests."""

    @abstractmethod
    def find_by_date_range(self, start_date: datetime, end_date: datetime) -> list[Request]:
        """Find requests within date range."""

    @abstractmethod
    def count_by_date_range(self, start_date: datetime, end_date: datetime) -> int:
        """Count requests within date range."""

    @abstractmethod
    def count_by_status_and_date_range(
        self, status: RequestStatus, start_date: datetime, end_date: datetime
    ) -> int:
        """Count requests by status within date range."""

    @abstractmethod
    def get_metrics_by_date_range(self, start_date: datetime, end_date: datetime) -> dict[str, int]:
        """Get aggregated metrics within date range."""

    def count_by_status(self) -> dict[str, int]:
        """Return ``{status_value: count}`` for all requests.

        Default implementation lists all requests and groups by status.
        Concrete implementations backed by SQL should override this with a
        single ``SELECT status, COUNT(*) GROUP BY status`` query.
        """
        counts: dict[str, int] = {}
        for req in self.find_all():
            key = str(getattr(req.status, "value", req.status))
            counts[key] = counts.get(key, 0) + 1
        return counts

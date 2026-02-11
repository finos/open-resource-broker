"""Request-related queries for CQRS implementation."""

from typing import Optional

from application.dto.base import BaseQuery


class ListRequestsQuery(BaseQuery):
    """Query to list requests with optional filtering."""

    status: Optional[str] = None
    template_id: Optional[str] = None
    limit: int = 50
    offset: int = 0
    filter_expressions: list[str] = []


class GetRequestHistoryQuery(BaseQuery):
    """Query to get request history."""

    request_id: str
    include_events: bool = True


class GetActiveRequestsQuery(BaseQuery):
    """Query to get all active requests."""

    template_id: Optional[str] = None
    limit: int = 100
    filter_expressions: list[str] = []


class GetRequestMetricsQuery(BaseQuery):
    """Query to get request metrics and statistics."""

    start_date: Optional[str] = None
    end_date: Optional[str] = None
    group_by: str = "status"

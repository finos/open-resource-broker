"""Request-related queries for CQRS implementation."""

from typing import Optional

from orb.application.dto.base import BaseQuery


class ListRequestsQuery(BaseQuery):
    """Query to list requests with optional filtering."""

    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    status: Optional[str] = None
    template_id: Optional[str] = None
    request_type: Optional[str] = None
    limit: int = 50
    offset: int = 0
    filter_expressions: list[str] = []
    # Server-side filter/sort — applied BEFORE the limit/offset slice.
    q: Optional[str] = None
    sort: Optional[str] = None



class GetRequestMetricsQuery(BaseQuery):
    """Query to get request metrics and statistics."""

    start_date: Optional[str] = None
    end_date: Optional[str] = None
    group_by: str = "status"

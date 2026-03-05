"""Request command factory for creating request-related commands and queries."""

from typing import Any, Optional

from application.dto.bulk_queries import GetMultipleRequestsQuery
from application.dto.commands import (
    CancelRequestCommand,
    CreateRequestCommand,
    CreateReturnRequestCommand,
)
from application.dto.queries import (
    GetRequestQuery,
    ListActiveRequestsQuery,
    ListReturnRequestsQuery,
)
from application.request.queries import ListRequestsQuery


class RequestCommandFactory:
    """Factory for creating request-related commands and queries."""

    def create_create_request_command(
        self,
        template_id: str,
        count: int,
        provider: Optional[str] = None,
        **kwargs: Any,
    ) -> CreateRequestCommand:
        """Create command to create machine request."""
        return CreateRequestCommand(
            template_id=template_id,
            requested_count=count,
        )

    def create_get_request_status_query(
        self,
        request_id: str,
        provider: Optional[str] = None,
        lightweight: bool = False,
        **kwargs: Any,
    ) -> GetRequestQuery:
        """Create query to get request status."""
        provider_name = kwargs.get("provider_name") or provider
        return GetRequestQuery(
            request_id=request_id,
            provider_name=provider_name,
            lightweight=lightweight,
        )

    def create_list_requests_query(
        self,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> ListRequestsQuery:
        """Create query to list requests."""
        provider_name = kwargs.get("provider_name") or provider
        return ListRequestsQuery(
            provider_name=provider_name,
            status=status,
            limit=limit if limit is not None else 50,
        )

    def create_cancel_request_command(self, request_id: str, **kwargs: Any) -> CancelRequestCommand:
        """Create command to cancel request."""
        reason = kwargs.get("reason") or "Cancelled by user"
        return CancelRequestCommand(request_id=request_id, reason=reason)

    def create_return_request_command(
        self,
        machine_ids: list[str],
        reason: Optional[str] = None,
        **kwargs: Any,
    ) -> CreateReturnRequestCommand:
        """Create command to return machines."""
        return CreateReturnRequestCommand(machine_ids=machine_ids)

    def create_list_return_requests_query(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = 50,
        offset: Optional[int] = 0,
        **kwargs: Any,
    ) -> ListReturnRequestsQuery:
        """Create query to list return requests."""
        return ListReturnRequestsQuery.model_validate(  # type: ignore[attr-defined]
            {
                "status": status,
                "limit": min(limit or 50, 1000),
                "offset": offset or 0,
            }
        )

    def create_list_active_requests_query(
        self,
        provider_name: Optional[str] = None,
        limit: Optional[int] = 50,
        offset: Optional[int] = 0,
        **kwargs: Any,
    ) -> ListActiveRequestsQuery:
        """Create query to list active requests."""
        return ListActiveRequestsQuery.model_validate(  # type: ignore[attr-defined]
            {
                "provider_name": provider_name,
                "limit": min(limit or 50, 1000),
                "offset": offset or 0,
            }
        )

    def create_get_multiple_requests_query(
        self,
        request_ids: list[str],
        provider_name: Optional[str] = None,
        lightweight: bool = False,
        include_machines: bool = True,
        **kwargs: Any,
    ) -> GetMultipleRequestsQuery:
        """Create query to get multiple requests by IDs."""
        return GetMultipleRequestsQuery(
            request_ids=request_ids,
            provider_name=provider_name,
            lightweight=lightweight,
            include_machines=include_machines,
        )

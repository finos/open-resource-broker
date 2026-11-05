"""Query DTOs for application layer."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from orb.application.interfaces.command_query import Query


class SyncAndGetRequestQuery(Query, BaseModel):
    """Query to get request details with live provider sync.

    This is a sync-on-read query: for non-terminal requests it refreshes machine
    state from the provider and persists any changes before returning the result.
    See ADR-0001 for rationale and naming convention.

    When ``lightweight=True`` the provider sync is skipped and only persisted
    data is returned — making that code path a pure read.
    """

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    request_id: str
    verbose: bool = False
    lightweight: bool = False
    skip_cache: bool = False


class SyncAndListActiveRequestsQuery(Query, BaseModel):
    """Query to list active requests with live provider sync per request.

    This is a sync-on-read query: each non-terminal request on the returned
    page is refreshed from the provider and any state changes are persisted
    before the response is assembled. See ADR-0001 for rationale and naming
    convention.
    """

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    status: Optional[str] = None
    template_id: Optional[str] = None
    filter_expressions: list[str] = []
    all_resources: bool = False
    limit: Optional[int] = 50  # Default: 50, Max: 1000
    offset: Optional[int] = 0
    # Server-side filter/sort — applied BEFORE the limit/offset slice so
    # pagination is honest (a q-match on row 9000 is still reachable).
    q: Optional[str] = None
    sort: Optional[str] = None  # "+field" / "-field"; prefix optional, "-" = desc


class SyncAndListReturnRequestsQuery(Query, BaseModel):
    """Query to list return requests with live provider sync per request.

    This is a sync-on-read query: each non-terminal return request is refreshed
    from the provider and any state changes (including status transitions) are
    persisted before the response is assembled. This prevents stale IN_PROGRESS
    states from triggering duplicate return attempts. See ADR-0001 for rationale
    and naming convention.
    """

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    status: Optional[str] = None
    requester_id: Optional[str] = None
    machine_names: list[str] = []
    filter_expressions: list[str] = []
    limit: Optional[int] = 50  # Default: 50, Max: 1000
    offset: Optional[int] = 0
    q: Optional[str] = None
    sort: Optional[str] = None
    # Keyset position (sort text, id) of the last row of the previous page
    after: Optional[tuple[str, str]] = None


class GetTemplateQuery(Query, BaseModel):
    """Query to get template details."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    template_id: str


class ListTemplatesQuery(Query, BaseModel):
    """Query to list available templates."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    provider_api: Optional[str] = None
    active_only: bool = True
    filter_expressions: list[str] = []
    limit: Optional[int] = 50  # Default: 50, Max: 1000
    offset: Optional[int] = 0
    q: Optional[str] = None
    sort: Optional[str] = None


class ValidateTemplateQuery(Query, BaseModel):
    """Query to validate template configuration."""

    model_config = ConfigDict(frozen=True)

    template_config: dict[str, Any] = {}
    template_id: Optional[str] = None  # For validating loaded templates


class GetMachineQuery(Query, BaseModel):
    """Query to get machine details."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    machine_id: str


class GetMachineHealthQuery(Query, BaseModel):
    """Query to get machine health.

    When ``refresh=True`` the health is refreshed live from the provider;
    otherwise the persisted health snapshot is returned.
    """

    model_config = ConfigDict(frozen=True)

    machine_id: str
    refresh: bool = False


class ListMachinesQuery(Query, BaseModel):
    """Query to list machines."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    request_id: Optional[str] = None
    status: Optional[str] = None
    active_only: bool = False
    filter_expressions: list[str] = []  # Generic filters
    all_resources: bool = False
    timestamp_format: Optional[str] = None
    limit: Optional[int] = 50  # Default: 50, Max: 1000
    offset: Optional[int] = 0
    q: Optional[str] = None
    sort: Optional[str] = None
    # When True, refresh each machine on the returned page from the
    # provider (one DescribeInstances per row). Off by default so list
    # endpoints stay cheap; callers that need authoritative state should
    # use the per-machine /status endpoint instead.
    sync: bool = False
    # Keyset position (sort text, id) of the last row of the previous page
    after: Optional[tuple[str, str]] = None


class GetActiveMachineCountQuery(Query, BaseModel):
    """Query to get count of active machines."""

    model_config = ConfigDict(frozen=True)


class GetConfigurationQuery(Query, BaseModel):
    """Query to get configuration value."""

    model_config = ConfigDict(frozen=True)

    key: str
    default: Optional[str] = None


class GetRequestSummaryQuery(Query, BaseModel):
    """Query to get summary of request status."""

    model_config = ConfigDict(frozen=True)

    request_id: str


class ValidateStorageQuery(Query, BaseModel):
    """Query to validate storage connectivity."""

    model_config = ConfigDict(frozen=True)

    strategy_name: Optional[str] = None
    timeout: int = 30


class ValidateMCPQuery(Query, BaseModel):
    """Query to validate MCP configuration."""

    model_config = ConfigDict(frozen=True)

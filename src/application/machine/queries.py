"""Machine-related queries for CQRS implementation."""

from typing import List, Optional

from application.dto.base import BaseQuery


class GetMachineStatusQuery(BaseQuery):
    """Query to get machine status."""

    machine_ids: List[str]
    include_metadata: bool = True


class ListMachinesQuery(BaseQuery):
    """Query to list machines with optional filtering."""

    template_id: Optional[str] = None
    status: Optional[str] = None
    limit: int = 50
    offset: int = 0


class GetMachineDetailsQuery(BaseQuery):
    """Query to get detailed machine information."""

    machine_id: str
    include_provider_data: bool = True


class GetMachineHealthQuery(BaseQuery):
    """Query to get machine health status."""

    machine_ids: List[str]
    check_connectivity: bool = True

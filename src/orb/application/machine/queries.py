"""Machine-related queries for CQRS implementation."""

from typing import Optional

from pydantic import Field

from orb.application.dto.base import BaseQuery


class GetMachineStatusQuery(BaseQuery):
    """Query to get machine status."""

    machine_ids: list[str]
    include_metadata: bool = True


class ListMachinesQuery(BaseQuery):
    """Query to list machines with optional filtering."""

    provider_name: Optional[str] = None
    template_id: Optional[str] = None
    status: Optional[str] = None
    request_id: Optional[str] = None
    filter_expressions: list[str] = Field(default_factory=list)  # Raw filter expressions from CLI
    timestamp_format: Optional[str] = None
    limit: int = 50
    offset: int = 0
    all_resources: bool = False


class GetMachineDetailsQuery(BaseQuery):
    """Query to get detailed machine information."""

    machine_id: str
    include_provider_data: bool = True


class GetMachineHealthQuery(BaseQuery):
    """Query to get machine health status."""

    machine_ids: list[str]
    check_connectivity: bool = True


class ConvertMachineStatusQuery(BaseQuery):
    """Query to convert a provider-specific state string to a domain MachineStatus."""

    provider_state: str
    provider_type: str


class ConvertBatchMachineStatusQuery(BaseQuery):
    """Query to convert multiple provider state strings to domain MachineStatus values."""

    # Each entry: {'state': str, 'provider_type': str}
    provider_states: list[dict[str, str]]


class ValidateProviderStateQuery(BaseQuery):
    """Query to check whether a provider state string maps to a known domain status."""

    provider_state: str
    provider_type: str

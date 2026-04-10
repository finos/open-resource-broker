"""Typed internal data structures for the GCP provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict

from orb.domain.request.aggregate import Request

if TYPE_CHECKING:
    from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
    from orb.providers.gcp.infrastructure.handlers.base_handler import GCPHandler


@dataclass(frozen=True)
class GCPInstanceRecord:
    """Normalized instance data returned from the Compute API."""

    name: str
    status: str | None = None
    self_link: str | None = None


@dataclass(frozen=True)
class GCPManagedInstanceRecord:
    """Normalized managed-instance data returned from a MIG."""

    instance_url: str
    instance_status: str | None = None
    current_action: str | None = None


class GCPHandlerContext(TypedDict, total=False):
    """Provider-owned context needed to operate on existing GCP resources."""

    project_id: str
    region: str
    zone: str
    scope: str
    mig_name: str
    instance_template_name: str
    provider_api: str


class GCPInstanceStatus(TypedDict):
    """Normalized status record surfaced by GCP handlers."""

    instance_id: str
    status: str
    provider_data: dict[str, str]


@dataclass(frozen=True)
class GCPFailedOperation:
    """Structured failure record for a per-target GCP batch operation."""

    target_id: str
    error_code: str
    error_message: str
    operation: str


@dataclass
class GCPCreateOutcome:
    """Provider-native acquire outcome returned by GCP handlers."""

    resource_ids: list[str] = field(default_factory=list)
    instances: list[GCPInstanceStatus] = field(default_factory=list)
    provider_data: dict[str, str | int | bool] = field(default_factory=dict)
    failed_operations: list[GCPFailedOperation] = field(default_factory=list)


@dataclass
class GCPMutationOutcome:
    """Provider-native mutation outcome returned by GCP handlers."""

    attempted_ids: list[str] = field(default_factory=list)
    successful_ids: list[str] = field(default_factory=list)
    operations: list[dict[str, str | None]] = field(default_factory=list)
    failed_operations: list[GCPFailedOperation] = field(default_factory=list)
    warning: str | None = None


@dataclass(frozen=True)
class GCPCreateOperationContext:
    """Typed inputs required to execute a GCP create operation."""

    template: GCPTemplate
    request: Request
    handler: GCPHandler
    count: int


@dataclass(frozen=True)
class GCPMutationOperationContext:
    """Typed inputs required to execute a GCP mutation operation."""

    handler: GCPHandler
    instance_ids: list[str]
    resource_ids: list[str]
    handler_context: GCPHandlerContext

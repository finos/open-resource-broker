"""Typed internal data structures for the GCP provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict

from orb.domain.request.aggregate import Request

if TYPE_CHECKING:
    from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
    from orb.providers.gcp.infrastructure.handlers.base_handler import GCPHandler


@dataclass(frozen=True)
class GCPInstanceRecord:
    """Normalized instance data returned from the Compute API.

    The normalisation strips protobuf-specific shapes (``ScalarMapContainer``
    for ``labels``, repeated-message wrappers for ``network_interfaces``) at
    the SDK boundary so downstream code can treat the record as plain Python
    values.
    """

    name: str
    status: str | None = None
    self_link: str | None = None
    instance_id: str | None = None
    machine_type: str | None = None
    creation_timestamp: str | None = None
    private_ip: str | None = None
    public_ip: str | None = None
    subnet_id: str | None = None
    vpc_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    provisioning_model: str | None = None


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


class GCPInstanceStatus(TypedDict, total=False):
    """Normalized status record surfaced by GCP handlers.

    Provider-specific fields (``zone``, ``region``) live under ``provider_data``
    per the ``metadata vs provider_data`` architecture rule; HostFactory reads
    ``cloud_host_id`` from there to emit the Symphony wire ``cloudHostId``.
    """

    instance_id: str
    status: str
    name: str
    private_ip: str | None
    public_ip: str | None
    launch_time: str | None
    instance_type: str | None
    tags: dict[str, str]
    price_type: str | None
    provider_data: dict[str, Any]


GCPProviderDataValue = str | int | bool | list[str] | list[dict[str, str]]
GCPProviderData = dict[str, GCPProviderDataValue]


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
    provider_data: GCPProviderData = field(default_factory=dict)
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

"""Typed GCP operation contexts owned by provider execution services."""

from __future__ import annotations

from dataclasses import dataclass

from orb.domain.request.aggregate import Request
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.infrastructure.handlers.base_handler import GCPHandler
from orb.providers.gcp.types import GCPHandlerContext


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
    requested_count: int | None = None

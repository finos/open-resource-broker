"""Base handler protocol for GCP provider runtime operations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from orb.domain.base.ports import LoggingPort
from orb.domain.request.aggregate import Request
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.infrastructure.compute_client import GCPComputeClient
from orb.providers.gcp.types import (
    GCPCreateOutcome,
    GCPHandlerContext,
    GCPInstanceStatus,
    GCPMutationOutcome,
)


class GCPHandler(ABC):
    """Base class for GCP runtime handlers."""

    def __init__(
        self,
        compute_client: GCPComputeClient,
        config: GCPProviderConfig,
        logger: LoggingPort,
    ) -> None:
        self._compute_client = compute_client
        self._config = config
        self._logger = logger

    @abstractmethod
    def acquire_hosts(self, request: Request, template: GCPTemplate) -> GCPCreateOutcome:
        """Create capacity for the request."""

    @abstractmethod
    def terminate_hosts(
        self,
        *,
        resource_ids: list[str],
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationOutcome:
        """Terminate provider-owned resources or instances."""

    @abstractmethod
    def check_hosts_status(
        self,
        *,
        resource_ids: list[str],
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> list[GCPInstanceStatus]:
        """Return normalized instance status records."""

    @abstractmethod
    def start_instances(
        self,
        *,
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationOutcome:
        """Start instances managed by this handler."""

    @abstractmethod
    def stop_instances(
        self,
        *,
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationOutcome:
        """Stop instances managed by this handler."""

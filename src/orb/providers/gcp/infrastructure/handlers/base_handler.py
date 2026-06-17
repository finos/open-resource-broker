"""Base handler protocol for GCP provider runtime operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from orb.domain.base.ports import LoggingPort
from orb.domain.request.aggregate import Request
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.domain.template.value_objects import GCPProvisioningModel
from orb.providers.gcp.infrastructure.compute_client import GCPComputeClient
from orb.providers.gcp.infrastructure.disk_types import normalize_boot_disk_type
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

    def _build_instance_configuration(
        self,
        *,
        template: GCPTemplate,
        machine_type: str,
        zone: str | None,
        payload_context: Literal["instance", "instance_template"],
    ) -> dict[str, Any]:
        """Return a kwargs dict suitable for both ``Instance`` and ``InstanceProperties``.

        The shape is the intersection of what ``compute_v1.Instance`` and
        ``compute_v1.InstanceProperties`` accept, so a single call can feed
        either standalone-VM creation (``InstancesClient.insert``) or a MIG
        instance template (``InstanceTemplatesClient.insert`` via
        ``InstanceProperties(**...)``).

        ``zone`` is required for standalone instances and used to resolve a
        zone-scoped boot-disk type; it is ``None`` for regional MIG templates,
        where the disk type is left in its template form.
        """
        from google.cloud import compute_v1

        disk_type = (
            str(template.boot_disk_type)
            if template.boot_disk_type is not None
            else "pd-balanced"
        )
        disk_size = template.boot_disk_size_gb or 50
        source_image = self._resolve_source_image(template)
        normalized_disk_type = normalize_boot_disk_type(
            disk_type,
            zone=zone,
            payload_context=payload_context,
        )

        payload: dict[str, Any] = {
            "machine_type": machine_type,
            "disks": [
                compute_v1.AttachedDisk(
                    boot=True,
                    auto_delete=True,
                    initialize_params=compute_v1.AttachedDiskInitializeParams(
                        source_image=source_image,
                        disk_type=normalized_disk_type,
                        disk_size_gb=disk_size,
                    ),
                )
            ],
            "labels": template.labels,
            "tags": compute_v1.Tags(items=template.network_tags),
        }

        network_interface = compute_v1.NetworkInterface()
        if template.network:
            network_interface.network = self._normalize_network_reference(template.network)
        if template.subnetwork:
            network_interface.subnetwork = self._normalize_subnetwork_reference(
                template.subnetwork,
                region=template.region.value,
            )
        if template.network or template.subnetwork:
            payload["network_interfaces"] = [network_interface]

        if template.service_account_email:
            payload["service_accounts"] = [
                compute_v1.ServiceAccount(
                    email=template.service_account_email,
                    scopes=template.service_account_scopes,
                )
            ]

        if template.provisioning_model == GCPProvisioningModel.SPOT:
            scheduling: dict[str, Any] = {
                "automatic_restart": False,
                "on_host_maintenance": "TERMINATE",
                "provisioning_model": "SPOT",
            }
            if payload_context == "instance":
                scheduling["instance_termination_action"] = "DELETE"
            payload["scheduling"] = compute_v1.Scheduling(**scheduling)

        return payload

    @staticmethod
    def _resolve_source_image(template: GCPTemplate) -> str | None:
        """Resolve the explicit image or image-family reference for a boot disk."""
        if template.source_image:
            return template.source_image
        if template.source_image_family and template.source_image_project:
            return (
                f"projects/{template.source_image_project}/global/images/family/"
                f"{template.source_image_family}"
            )
        return None

    @staticmethod
    def _normalize_network_reference(network: str) -> str:
        """Return a Compute Engine network reference accepted by insert requests."""
        if _is_gcp_resource_reference(network):
            return network
        return f"global/networks/{network}"

    @staticmethod
    def _normalize_subnetwork_reference(subnetwork: str, *, region: str) -> str:
        """Return a Compute Engine subnetwork reference accepted by insert requests."""
        if _is_gcp_resource_reference(subnetwork):
            return subnetwork
        return f"regions/{region}/subnetworks/{subnetwork}"

    def _operation_wait_timeout_seconds(self) -> int:
        """Return a bounded wait time for long-running GCP operations."""
        per_attempt_timeout = self._config.connect_timeout + self._config.read_timeout
        retry_budget = max(1, self._config.max_retries)
        return max(1, per_attempt_timeout * retry_budget)


def _is_gcp_resource_reference(value: str) -> bool:
    """Return true when the value is already a GCP resource path or self-link."""
    return value.startswith(("global/", "regions/", "projects/", "https://", "http://"))

"""Compute Engine single-instance handler."""

from __future__ import annotations

# noinspection PyTypeHints
# PyCharm treats google-cloud-compute generated proto classes as Any in annotations here.
import uuid
from typing import TYPE_CHECKING

from orb.domain.request.aggregate import Request
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.exceptions import GCPValidationError
from orb.providers.gcp.infrastructure.disk_types import normalize_boot_disk_type
from orb.providers.gcp.infrastructure.handlers.base_handler import GCPHandler
from orb.providers.gcp.types import (
    GCPCreateHandlerResult,
    GCPHandlerContext,
    GCPInstanceStatus,
    GCPMutationResult,
)

if TYPE_CHECKING:
    from google.cloud.compute_v1.types import Instance


class GCPSingleVMHandler(GCPHandler):
    """Create and manage standalone Compute Engine instances."""

    def acquire_hosts(self, request: Request, template: GCPTemplate) -> GCPCreateHandlerResult:
        zone = self._template_zone(template)
        instances: list[GCPInstanceStatus] = []
        resource_ids: list[str] = []

        for _ in range(request.requested_count):
            instance_name = f"gcp-{template.template_id}-{uuid.uuid4().hex[:8]}"
            operation = self._compute_client.create_instance(
                zone=zone,
                body=self._build_instance_payload(instance_name, template),
            )
            resource_ids.append(instance_name)
            instances.append(
                {
                    "instance_id": instance_name,
                    "status": "PROVISIONING",
                    "provider_data": {"zone": zone, "operation_name": operation.name or ""},
                }
            )

        return {
            "resource_ids": resource_ids,
            "instances": instances,
            "provider_data": {
                "zone": zone,
                "submitted_count": len(resource_ids),
                "operation_status": "submitted",  # type: ignore[typeddict-item]
            },
        }

    def terminate_hosts(
        self,
        *,
        resource_ids: list[str],
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationResult:
        zone = self._require_zone(context)
        target_ids = instance_ids or resource_ids
        operations: list[dict[str, str | None]] = []
        for instance_name in target_ids:
            response = self._compute_client.delete_instance(zone=zone, instance_name=instance_name)
            operations.append({"instance_id": instance_name, "operation_name": response.name})
        return {"terminated_ids": target_ids, "operations": operations}

    def check_hosts_status(
        self,
        *,
        resource_ids: list[str],
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> list[GCPInstanceStatus]:
        zone = self._require_zone(context)
        target_ids = instance_ids or resource_ids
        results: list[GCPInstanceStatus] = []
        for instance_name in target_ids:
            instance = self._compute_client.get_instance(zone=zone, instance_name=instance_name)
            results.append(
                {
                    "instance_id": instance_name,
                    "status": instance.status or "UNKNOWN",
                    "provider_data": {
                        "zone": zone,
                        "resource_id": instance.self_link or instance_name,
                    },
                }
            )
        return results

    def start_instances(
        self,
        *,
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationResult:
        zone = self._require_zone(context)
        operations: list[dict[str, str | None]] = []
        for instance_name in instance_ids:
            response = self._compute_client.start_instance(zone=zone, instance_name=instance_name)
            operations.append({"instance_id": instance_name, "operation_name": response.name})
        return {"started_instance_ids": instance_ids, "operations": operations}

    def stop_instances(
        self,
        *,
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationResult:
        zone = self._require_zone(context)
        operations: list[dict[str, str | None]] = []
        for instance_name in instance_ids:
            response = self._compute_client.stop_instance(zone=zone, instance_name=instance_name)
            operations.append({"instance_id": instance_name, "operation_name": response.name})
        return {"stopped_instance_ids": instance_ids, "operations": operations}

    def _build_instance_payload(self, instance_name: str, template: GCPTemplate) -> Instance:
        from google.cloud import compute_v1

        source_image = template.source_image
        if not source_image and template.source_image_family and template.source_image_project:
            source_image = (
                f"projects/{template.source_image_project}/global/images/family/"
                f"{template.source_image_family}"
            )

        disk_type = template.boot_disk_type or "pd-balanced"
        disk_size = template.boot_disk_size_gb or 50
        zone = self._template_zone(template)
        normalized_disk_type = normalize_boot_disk_type(disk_type, zone=zone)
        machine_type = (
            template.instance_type
            if template.instance_type.startswith("zones/")
            else f"zones/{zone}/machineTypes/{template.instance_type}"
        )
        payload = compute_v1.Instance(
            name=instance_name,
            machine_type=machine_type,
            disks=[
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
            labels=template.labels,
            tags=compute_v1.Tags(items=template.network_tags),
        )
        network_interface = compute_v1.NetworkInterface()
        if template.network:
            network_interface.network = template.network
        if template.subnetwork:
            network_interface.subnetwork = template.subnetwork
        if template.network or template.subnetwork:
            payload.network_interfaces = [network_interface]
        if template.service_account_email:
            payload.service_accounts = [
                compute_v1.ServiceAccount(
                    email=template.service_account_email,
                    scopes=template.service_account_scopes,
                )
            ]
        if template.provisioning_model.value == "SPOT":
            payload.scheduling = compute_v1.Scheduling(
                provisioning_model="SPOT",
                instance_termination_action="DELETE",
            )
        return payload

    @staticmethod
    def _require_zone(context: GCPHandlerContext) -> str:
        zone = context.get("zone")
        if not zone:
            raise GCPValidationError("zone is required for SingleVM operations")
        return str(zone)

    @staticmethod
    def _template_zone(template: GCPTemplate) -> str:
        if len(template.zones) != 1:
            raise GCPValidationError("SingleVM templates require exactly one explicit zone")
        return str(template.zones[0])

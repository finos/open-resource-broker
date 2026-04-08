"""Managed Instance Group handler for GCP."""

from __future__ import annotations

# noinspection PyTypeHints
# PyCharm treats google-cloud-compute generated proto classes as Any in annotations here.
import uuid
from typing import TYPE_CHECKING

from orb.domain.request.aggregate import Request
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.domain.template.value_objects import GCPMIGScope
from orb.providers.gcp.infrastructure.disk_types import normalize_boot_disk_type
from orb.providers.gcp.infrastructure.handlers.base_handler import GCPHandler
from orb.providers.gcp.types import (
    GCPCreateHandlerResult,
    GCPHandlerContext,
    GCPInstanceStatus,
    GCPMutationResult,
)

if TYPE_CHECKING:
    from google.cloud.compute_v1.types import InstanceGroupManager, InstanceTemplate


class GCPManagedInstanceGroupHandler(GCPHandler):
    """Create and manage zonal or regional Managed Instance Groups."""

    def acquire_hosts(self, request: Request, template: GCPTemplate) -> GCPCreateHandlerResult:
        mig_name = template.mig_name or f"orb-mig-{template.template_id}-{uuid.uuid4().hex[:8]}"
        template_name = (
            f"{template.instance_template_name_prefix or 'orb'}-{template.template_id}-{uuid.uuid4().hex[:8]}"
        )
        self._compute_client.create_instance_template(
            template_name=template_name,
            body=self._build_instance_template_payload(template, template_name),
        )

        if template.mig_scope == GCPMIGScope.REGIONAL:
            region = str(template.region)
            response = self._compute_client.create_regional_mig(
                region=region,
                mig_name=mig_name,
                body=self._build_regional_mig_payload(
                    template=template,
                    template_name=template_name,
                    target_size=request.requested_count,
                ),
            )
            location_context = {"region": region, "scope": template.mig_scope.value}
        else:
            zone = str(template.zones[0])
            response = self._compute_client.create_zonal_mig(
                zone=zone,
                mig_name=mig_name,
                body=self._build_zonal_mig_payload(
                    template=template,
                    template_name=template_name,
                    target_size=request.requested_count,
                ),
            )
            location_context = {"zone": zone, "scope": template.mig_scope.value}

        return {
            "resource_ids": [mig_name],
            "instances": [],
            "provider_data": {
                "mig_name": mig_name,
                "instance_template_name": template_name,
                "target_size": request.requested_count,
                "operation_name": response.name or "",
                "operation_status": "submitted",  # type: ignore[typeddict-item]
                **location_context,
            },
        }

    def terminate_hosts(
        self,
        *,
        resource_ids: list[str],
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationResult:
        mig_name = self._require_mig_name(resource_ids, context)
        template_name = context.get("instance_template_name")
        scope = str(context.get("scope") or GCPMIGScope.REGIONAL.value)

        if instance_ids:
            instance_urls = [self._instance_url(instance_id, context=context) for instance_id in instance_ids]
            if scope == GCPMIGScope.ZONAL.value:
                response = self._compute_client.delete_zonal_managed_instances(
                    zone=self._require_zone(context),
                    mig_name=mig_name,
                    instance_urls=instance_urls,
                )
            else:
                response = self._compute_client.delete_regional_managed_instances(
                    region=self._require_region(context),
                    mig_name=mig_name,
                    instance_urls=instance_urls,
                )
            return {
                "terminated_ids": instance_ids,
                "operations": [{"operation_name": response.name, "mig_name": mig_name}],
            }

        if scope == GCPMIGScope.ZONAL.value:
            response = self._compute_client.delete_zonal_mig(
                zone=self._require_zone(context),
                mig_name=mig_name,
            )
        else:
            response = self._compute_client.delete_regional_mig(
                region=self._require_region(context),
                mig_name=mig_name,
            )

        if template_name:
            try:
                self._compute_client.delete_instance_template(template_name=str(template_name))
            except Exception:
                self._logger.debug("Best-effort instance template cleanup failed for %s", template_name)

        return {
            "terminated_ids": [mig_name],
            "operations": [{"operation_name": response.name, "mig_name": mig_name}],
        }

    def check_hosts_status(
        self,
        *,
        resource_ids: list[str],
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> list[GCPInstanceStatus]:
        mig_name = self._require_mig_name(resource_ids, context)
        scope = str(context.get("scope") or GCPMIGScope.REGIONAL.value)
        if scope == GCPMIGScope.ZONAL.value:
            payload = self._compute_client.list_zonal_managed_instances(
                zone=self._require_zone(context),
                mig_name=mig_name,
            )
        else:
            payload = self._compute_client.list_regional_managed_instances(
                region=self._require_region(context),
                mig_name=mig_name,
            )

        results: list[GCPInstanceStatus] = []
        for instance in payload:
            instance_name = instance.instance_url.rsplit("/", 1)[-1]
            if instance_ids and instance_name not in instance_ids:
                continue
            results.append(
                {
                    "instance_id": instance_name,
                    "status": instance.instance_status or instance.current_action or "UNKNOWN",
                    "provider_data": {
                        "resource_id": mig_name,
                        "scope": scope,
                        "instance_url": instance.instance_url,
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
        return {
            "started_instance_ids": [],
            "operations": [],
            "warning": "MIG-managed instances follow group policy; start is not supported directly",
        }

    def stop_instances(
        self,
        *,
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationResult:
        return {
            "stopped_instance_ids": [],
            "operations": [],
            "warning": "MIG-managed instances follow group policy; stop is not supported directly",
        }

    def _build_instance_template_payload(
        self,
        template: GCPTemplate,
        template_name: str,
    ) -> InstanceTemplate:
        from google.cloud import compute_v1

        disk_type = template.boot_disk_type or "pd-balanced"
        disk_size = template.boot_disk_size_gb or 50
        source_image = template.source_image
        if not source_image and template.source_image_family and template.source_image_project:
            source_image = (
                f"projects/{template.source_image_project}/global/images/family/"
                f"{template.source_image_family}"
            )
        normalized_disk_type = normalize_boot_disk_type(
            disk_type,
            zone=str(template.zones[0]) if template.zones else None,
        )
        properties = compute_v1.InstanceProperties(
            machine_type=template.instance_type,
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
            properties.network_interfaces = [network_interface]
        if template.service_account_email:
            properties.service_accounts = [
                compute_v1.ServiceAccount(
                    email=template.service_account_email,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
            ]
        if template.provisioning_model.value == "SPOT":
            properties.scheduling = compute_v1.Scheduling(
                provisioning_model="SPOT",
                instance_termination_action="DELETE",
            )
        return compute_v1.InstanceTemplate(properties=properties)

    def _build_regional_mig_payload(
        self,
        *,
        template: GCPTemplate,
        template_name: str,
        target_size: int,
    ) -> InstanceGroupManager:
        from google.cloud import compute_v1

        distribution: compute_v1.DistributionPolicy | None = None
        if template.zones:
            distribution = compute_v1.DistributionPolicy(
                zones=[
                    {"zone": f"projects/{template.project_id}/zones/{zone}"}
                    for zone in template.zones
                ]
            )
        return compute_v1.InstanceGroupManager(
            base_instance_name=template.template_id,
            instance_template=f"projects/{template.project_id}/global/instanceTemplates/{template_name}",
            target_size=target_size,
            distribution_policy=distribution,
        )

    def _build_zonal_mig_payload(
        self,
        *,
        template: GCPTemplate,
        template_name: str,
        target_size: int,
    ) -> InstanceGroupManager:
        from google.cloud import compute_v1

        return compute_v1.InstanceGroupManager(
            base_instance_name=template.template_id,
            instance_template=f"projects/{template.project_id}/global/instanceTemplates/{template_name}",
            target_size=target_size,
        )

    @staticmethod
    def _require_mig_name(resource_ids: list[str], context: GCPHandlerContext) -> str:
        mig_name = (resource_ids[0] if resource_ids else None) or context.get("mig_name")
        if not mig_name:
            raise ValueError("MIG operations require a mig resource id")
        return str(mig_name)

    @staticmethod
    def _require_region(context: GCPHandlerContext) -> str:
        region = context.get("region")
        if not region:
            raise ValueError("region is required for regional MIG operations")
        return str(region)

    @staticmethod
    def _require_zone(context: GCPHandlerContext) -> str:
        zone = context.get("zone")
        if not zone:
            raise ValueError("zone is required for zonal MIG operations")
        return str(zone)

    @staticmethod
    def _instance_url(instance_id: str, *, context: GCPHandlerContext) -> str:
        existing = str(instance_id)
        if existing.startswith("https://") or existing.startswith("projects/"):
            return existing
        zone = context.get("zone")
        project_id = context.get("project_id")
        if not zone or not project_id:
            return existing
        return f"projects/{project_id}/zones/{zone}/instances/{existing}"

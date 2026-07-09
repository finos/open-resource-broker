"""GCP template extension defaults."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from orb.providers.gcp.constants import (
    DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES,
)
from orb.providers.gcp.domain.template.service_account_scopes import (
    GCPServiceAccountScopes,
)


class GCPTemplateExtensionConfig(BaseModel):
    """GCP-specific template defaults."""

    model_config = ConfigDict(extra="ignore")

    provider_api: str = Field(default="MIG", description="Default GCP provider API")
    machine_type: str = Field(default="e2-standard-4", description="Default GCP machine type")
    project_id: Optional[str] = Field(default=None, description="Default GCP project ID")
    region: Optional[str] = Field(default=None, description="Default GCP region")
    zones: list[str] = Field(default_factory=list, description="Default GCP zones")
    mig_scope: str = Field(default="regional", description="Default managed instance group scope")
    network: Optional[str] = Field(default=None, description="Default VPC network")
    subnetwork: Optional[str] = Field(default=None, description="Default VPC subnetwork")
    boot_disk_size_gb: int = Field(default=50, ge=10, description="Boot disk size in GiB")
    boot_disk_type: str = Field(default="pd-balanced", description="Boot disk type")
    service_account_email: Optional[str] = Field(
        default=None, description="Default service account email"
    )
    service_account_scopes: GCPServiceAccountScopes = Field(
        default_factory=lambda: list(DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES),
        description="Default OAuth scopes for attached service accounts",
    )
    network_tags: list[str] = Field(default_factory=list, description="Default network tags")
    labels: dict[str, str] = Field(default_factory=dict, description="Default instance labels")
    provisioning_model: str = Field(
        default="STANDARD",
        description="Default GCP provisioning model (STANDARD or SPOT)",
    )
    source_image_family: Optional[str] = Field(
        default="debian-12", description="Default source image family"
    )
    source_image_project: Optional[str] = Field(
        default="debian-cloud", description="Default source image project"
    )
    source_image: Optional[str] = Field(default=None, description="Default source image self-link")
    mig_name: Optional[str] = Field(default=None, description="Default managed instance group name")
    instance_template_name_prefix: Optional[str] = Field(
        default="orb", description="Default prefix for generated instance templates"
    )

    def to_template_defaults(self) -> dict[str, Any]:
        """Convert extension defaults into template defaults."""
        defaults: dict[str, Any] = {
            "provider_api": self.provider_api,
            "instance_type": self.machine_type,
            "root_device_volume_size": self.boot_disk_size_gb,
            "volume_type": self.boot_disk_type,
            "boot_disk_size_gb": self.boot_disk_size_gb,
            "boot_disk_type": self.boot_disk_type,
            "metadata": {
                "gcp_provisioning_model": self.provisioning_model,
            },
            "tags": self.labels,
            "labels": self.labels,
        }
        if self.service_account_email:
            defaults["instance_profile"] = self.service_account_email
            defaults["service_account_email"] = self.service_account_email
        if self.service_account_scopes:
            defaults["service_account_scopes"] = self.service_account_scopes
        if self.network_tags:
            defaults["network_tags"] = self.network_tags
        if self.project_id:
            defaults["project_id"] = self.project_id
        if self.region:
            defaults["region"] = self.region
        if self.zones:
            defaults["zones"] = self.zones
        if self.mig_scope:
            defaults["mig_scope"] = self.mig_scope
        if self.network:
            defaults["network"] = self.network
        if self.subnetwork:
            defaults["subnetwork"] = self.subnetwork
        if self.source_image:
            defaults["source_image"] = self.source_image
        if self.source_image_family:
            defaults["source_image_family"] = self.source_image_family
        if self.source_image_project:
            defaults["source_image_project"] = self.source_image_project
        if self.mig_name:
            defaults["mig_name"] = self.mig_name
        if self.instance_template_name_prefix:
            defaults["instance_template_name_prefix"] = self.instance_template_name_prefix
        return defaults

"""GCP template extension defaults."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from orb.providers.gcp.constants import DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES


class GCPTemplateExtensionConfig(BaseModel):
    """GCP-specific template defaults."""

    provider_api: str = Field(default="MIG", description="Default GCP provider API")
    machine_type: str = Field(default="e2-standard-4", description="Default GCP machine type")
    boot_disk_size_gb: int = Field(default=50, ge=10, description="Boot disk size in GiB")
    boot_disk_type: str = Field(default="pd-balanced", description="Boot disk type")
    service_account_email: Optional[str] = Field(
        default=None, description="Default service account email"
    )
    service_account_scopes: list[str] = Field(
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
            "metadata": {
                "gcp_provisioning_model": self.provisioning_model,
            },
            "tags": self.labels,
        }
        if self.service_account_email:
            defaults["instance_profile"] = self.service_account_email
        if self.service_account_scopes:
            defaults["service_account_scopes"] = self.service_account_scopes
        if self.network_tags:
            defaults["network_tags"] = self.network_tags
        if self.source_image_family:
            defaults["source_image_family"] = self.source_image_family
        if self.source_image_project:
            defaults["source_image_project"] = self.source_image_project
        if self.instance_template_name_prefix:
            defaults["instance_template_name_prefix"] = self.instance_template_name_prefix
        return defaults

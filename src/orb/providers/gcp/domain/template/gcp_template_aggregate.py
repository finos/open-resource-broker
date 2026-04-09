"""GCP template aggregate."""

from __future__ import annotations

from typing import Optional

from pydantic import ConfigDict, Field, model_validator

from orb.domain.template.template_aggregate import Template
from orb.providers.gcp.constants import DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES
from orb.providers.gcp.domain.template.value_objects import (
    GCPMIGScope,
    GCPProjectId,
    GCPProviderApi,
    GCPProvisioningModel,
    GCPRegion,
    GCPZone,
)


class GCPTemplate(Template):
    """GCP-specific template contract for MIG and Single VM provisioning."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    provider_api: GCPProviderApi = GCPProviderApi.MIG
    project_id: GCPProjectId
    region: GCPRegion
    zones: list[GCPZone] = Field(default_factory=list)
    mig_scope: GCPMIGScope = GCPMIGScope.REGIONAL
    instance_type: str = Field(..., description="Compute Engine machine type")
    network: Optional[str] = None
    subnetwork: Optional[str] = None
    service_account_email: Optional[str] = None
    service_account_scopes: list[str] = Field(
        default_factory=lambda: list(DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES)
    )
    labels: dict[str, str] = Field(default_factory=dict)
    network_tags: list[str] = Field(default_factory=list)
    provisioning_model: GCPProvisioningModel = GCPProvisioningModel.STANDARD
    source_image: Optional[str] = None
    source_image_family: Optional[str] = None
    source_image_project: Optional[str] = None
    boot_disk_type: Optional[str] = None
    boot_disk_size_gb: Optional[int] = Field(default=None, ge=10)
    mig_name: Optional[str] = None
    instance_template_name_prefix: Optional[str] = None

    def __init__(self, **data):
        data["provider_type"] = "gcp"
        super().__init__(**data)

    @model_validator(mode="after")
    def validate_gcp_template(self) -> GCPTemplate:
        """Validate GCP-specific template semantics."""
        if self.provider_api == GCPProviderApi.MIG:
            if self.max_instances <= 0:
                raise ValueError("MIG templates require max_instances > 0")
            # Regional and zonal MIGs have different placement semantics:
            # https://cloud.google.com/compute/docs/instance-groups/distributing-instances-with-regional-instance-groups
            # https://cloud.google.com/compute/docs/instance-groups/creating-groups-of-managed-instances
            if self.mig_scope == GCPMIGScope.ZONAL and len(self.zones) != 1:
                raise ValueError("zonal MIG templates require exactly one zone")
            if self.mig_scope == GCPMIGScope.REGIONAL and self.zones and len(self.zones) < 2:
                raise ValueError(
                    "regional MIG templates should use at least two zones when zones are specified"
                )
        elif self.provider_api == GCPProviderApi.SINGLE_VM:
            if self.max_instances != 1:
                raise ValueError("SingleVM templates require max_instances == 1")
            if len(self.zones) != 1:
                raise ValueError("SingleVM templates require exactly one explicit zone")

        if not self.source_image and not (
            self.source_image_family and self.source_image_project
        ):
            raise ValueError(
                "GCP templates require source_image or source_image_family + source_image_project"
            )

        # Spot instances in Compute Engine use provisioningModel=SPOT:
        # https://cloud.google.com/compute/docs/instances/spot
        if self.price_type == "spot" and self.provisioning_model != GCPProvisioningModel.SPOT:
            raise ValueError("spot price_type requires provisioning_model='SPOT'")

        return self

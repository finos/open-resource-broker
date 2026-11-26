"""OCI fields preserved through template defaults and serialization."""

from typing import Any, Literal

from pydantic import AliasChoices, Field, model_validator

from orb.providers.base.template_extension import ProviderTemplateExtensionBase
from orb.providers.oci.configuration.boot_volume import (
    BootVolumeVPUsPerGB,
    normalize_boot_volume_performance_alias,
)
from orb.providers.oci.configuration.instance_options import OCIInstanceOptions


class OCITemplateExtensionConfig(ProviderTemplateExtensionBase):
    provider_api: str = "OCICompute"
    compartment_id: str | None = None
    availability_domain: str | None = None
    compute_cluster_id: str | None = None
    agent_config: dict[str, Any] | None = None
    instance_options: OCIInstanceOptions | None = None
    shape: str | None = None
    pricing: dict[str, Any] | None = None
    shape_config: dict[str, Any] | None = None
    ocpus: float | None = Field(default=None, gt=0)
    memory_gbs: float | None = Field(default=None, gt=0)
    boot_volume_gbs: float | None = Field(default=None, gt=0)
    boot_volume_vpus_per_gb: BootVolumeVPUsPerGB | None = Field(
        default=None,
        validation_alias=AliasChoices("boot_volume_vpus_per_gb", "bootVolumeVpusPerGB"),
    )
    capacity_type: Literal["ondemand", "preemptible"] = "ondemand"
    preemptible_preserve_boot_volume: bool = False
    ssh_authorized_keys: str | None = None
    form_factor: str | None = None
    cpu_vendor: str | None = None
    generation: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_boot_performance(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return normalize_boot_volume_performance_alias(data)
        return data

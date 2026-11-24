"""OCI fields preserved through template defaults and serialization."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orb.providers.base.template_extension import ProviderTemplateExtensionBase

# OCI Block Volume Performance, tables for VM shapes using paravirtualized attachments.
_PV_LIMIT_16_SHAPES = {
    "VM.DenseIO.E4.Flex",
    "VM.DenseIO.E5.Flex",
    "VM.DenseIO2.8",
    "VM.Optimized3.Flex",
    "VM.Standard.A1.Flex",
    "VM.Standard.E2.4",
    "VM.Standard.E2.8",
    "VM.Standard.E3.Flex",
    "VM.Standard.E4.Flex",
    "VM.Standard.E5.Flex",
    "VM.Standard.E6.Flex",
    "VM.Standard2.8",
    "VM.Standard3.Flex",
}


class OCISecondaryVolume(BaseModel):
    """One new, blank block volume per worker; list order identifies each slot."""

    model_config = ConfigDict(extra="forbid", strict=True)

    size_in_gbs: int = Field(ge=50, le=32768)
    # Ultra High Performance requires multipath attachment support.
    vpus_per_gb: int | None = Field(default=None, ge=0, le=20, multiple_of=10)
    display_name: str | None = Field(default=None, min_length=1)
    delete_on_return: bool


class OCITemplateExtensionConfig(ProviderTemplateExtensionBase):
    provider_api: str = "OCICompute"
    compartment_id: str | None = None
    availability_domain: str | None = None
    compute_cluster_id: str | None = None
    agent_config: dict[str, Any] | None = None
    shape: str | None = None
    pricing: dict[str, Any] | None = None
    shape_config: dict[str, Any] | None = None
    ocpus: float | None = Field(default=None, gt=0)
    memory_gbs: float | None = Field(default=None, gt=0)
    boot_volume_gbs: float | None = Field(default=None, gt=0)
    secondary_volumes: list[OCISecondaryVolume] | None = Field(
        default=None, max_length=32, strict=True
    )
    capacity_type: Literal["ondemand", "preemptible"] = "ondemand"
    preemptible_preserve_boot_volume: bool = False
    ssh_authorized_keys: str | None = None
    form_factor: str | None = None
    cpu_vendor: str | None = None
    generation: str | None = None

    @model_validator(mode="after")
    def validate_secondary_volume_count(self) -> "OCITemplateExtensionConfig":
        if self.secondary_volumes and len(self.secondary_volumes) > 16:
            # getattr: full templates supply machine_types; standalone OCI defaults omit it.
            shape = self.shape or next(iter(getattr(self, "machine_types", {}) or {}), None)
            if shape in _PV_LIMIT_16_SHAPES:
                raise ValueError(
                    f"secondary_volumes exceeds the 16 paravirtualized attachments for {shape}"
                )
        return self

"""OCI fields preserved through template defaults and serialization."""

from typing import Any, Literal

from pydantic import Field

from orb.providers.base.template_extension import ProviderTemplateExtensionBase


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
    capacity_type: Literal["ondemand", "preemptible"] = "ondemand"
    fallback_to_ondemand: bool = False
    preemptible_preserve_boot_volume: bool = False
    ssh_authorized_keys: str | None = None
    form_factor: str | None = None
    cpu_vendor: str | None = None
    generation: str | None = None

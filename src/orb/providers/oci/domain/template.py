"""OCI template with provider-specific launch configuration."""

from typing import Any

from pydantic import StrictBool, model_validator

from orb.domain.template.template_aggregate import Template
from orb.providers.oci.configuration.boot_volume import normalize_boot_volume_performance_alias
from orb.providers.oci.configuration.template_extension import OCITemplateExtensionConfig


class OCITemplate(Template, OCITemplateExtensionConfig):
    """Preserve OCI fields across the template repository and scheduler."""

    provider_type: str = "oci"
    provider_api: str = "OCICompute"
    public_ip_assignment: StrictBool | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_shape(cls, data: Any) -> Any:
        """Expose single-shape input through ORB's canonical machine_types field."""
        if isinstance(data, dict):
            data = normalize_boot_volume_performance_alias(data)
            nested = data.pop("provider_config", None)
            if isinstance(nested, dict):
                for key in OCITemplateExtensionConfig.model_fields:
                    if key == "boot_volume_vpus_per_gb":
                        # Already resolved with configuration/metadata precedence.
                        continue
                    if data.get(key) is None and nested.get(key) is not None:
                        data[key] = nested[key]
        if isinstance(data, dict) and not data.get("machine_types"):
            shape = data.get("shape") or data.get("machine_type") or data.get("instance_type")
            if shape:
                return {**data, "machine_types": {shape: 1}}
        return data

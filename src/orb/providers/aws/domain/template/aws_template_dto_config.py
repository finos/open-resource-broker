"""AWS-specific typed DTO configuration for TemplateDTO serialisation."""

from typing import Any, Optional

from pydantic import Field

from orb.providers.base.template_extension import ProviderTemplateExtensionBase


class AWSTemplateDTOConfig(ProviderTemplateExtensionBase):
    """Typed container for AWS-specific fields on TemplateDTO.

    This class holds only the fields that are AWS-specific and were
    previously scattered between the top-level TemplateDTO attributes
    and the opaque ``metadata`` dict.  It is registered with
    ``TemplateExtensionRegistry`` so that ``TemplateDTO.from_domain``
    can delegate construction to the registry rather than containing
    AWS knowledge directly.
    """

    # EC2 Fleet / Spot Fleet configuration
    fleet_type: Optional[str] = Field(None, description="Fleet type (maintain, request, instant)")
    fleet_role: Optional[str] = Field(None, description="IAM role ARN for Spot Fleet")
    percent_on_demand: Optional[int] = Field(
        None, description="Percentage of On-Demand capacity in a heterogeneous fleet"
    )

    # Launch template reference
    launch_template_id: Optional[str] = Field(
        None, description="EC2 Launch Template ID to use instead of inline spec"
    )

    # Attribute-based instance selection payload (already serialised to dict)
    abis_instance_requirements: Optional[dict[str, Any]] = Field(
        None, description="InstanceRequirements dict for attribute-based instance selection"
    )

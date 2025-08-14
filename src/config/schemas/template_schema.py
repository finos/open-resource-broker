"""Template configuration schemas - provider agnostic core configuration."""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class TemplateConfig(BaseModel):
    """Core template configuration - provider agnostic.

    This schema contains only generic template configuration that applies
    to all providers. Provider-specific configuration should be handled
    through provider extensions and hierarchical defaults.
    """

    # Core template configuration
    max_number: int = Field(10, description="Maximum number of instances per request")

    # Template file paths
    templates_file_path: str = Field(
        "config/templates.json", description="Path to templates file")
    legacy_templates_file_path: str = Field(
        "config/awsprov_templates.json", description="Path to legacy templates file"
    )

    # Generic template metadata
    default_attributes: Dict[str, List[str]] = Field(
        default_factory=dict, description="Default attributes for templates"
    )
    default_instance_tags: Dict[str, str] = Field(
        default_factory=dict, description="Default instance tags"
    )
    tags: Dict[str, str] = Field(default_factory=dict, description="Tags for templates")

    # Multi-provider template defaults (generic)
    default_provider_type: Optional[str] = Field(
        None, description="Default provider type (aws, provider1, provider2)"
    )
    default_provider_name: Optional[str] = Field(
        None, description="Default provider instance name")
    default_provider_api: str = Field("EC2Fleet", description="Default provider API")

    # Generic pricing configuration
    default_price_type: str = Field(
        "ondemand", description="Default pricing type (ondemand, spot, heterogeneous)"
    )

    # Note: All AWS-specific fields have been moved to AWS provider extensions:
    # - AMI resolution configuration -> AWS provider extensions
    # - Fleet roles, spot pricing, volume types -> AWS template extensions
    # - Subnet IDs, security groups -> AWS provider defaults
    # - Instance types, allocation strategies -> AWS provider defaults

    # Note: All deprecated optional fields have been removed:
    # - default_image_id, default_instance_type -> Use hierarchical provider defaults
    # - subnet_ids, security_group_ids -> Use provider-specific configuration
    # - default_max_number -> Use max_number
    # - SSM parameter prefix -> AWS-specific, moved to AWS extensions

"""Template configuration value object - core template domain logic."""

import logging
import warnings
from datetime import datetime
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)


class Template(BaseModel):
    """Template configuration value object with both snake_case and camelCase support via aliases."""

    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        populate_by_name=True,  # Allow both field names and aliases
    )

    # Core template fields (provider-agnostic)
    template_id: str
    name: Optional[str] = None
    description: Optional[str] = None

    # Instance configuration
    machine_type: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("machine_type", "instance_type"),
        deprecated="use 'machine_type' instead of 'instance_type'",
    )
    image_id: Optional[str] = None
    max_instances: int = 1

    # Network configuration
    subnet_ids: list[str] = Field(default_factory=list)
    security_group_ids: list[str] = Field(default_factory=list)

    # Pricing and allocation
    price_type: str = "ondemand"
    allocation_strategy: Optional[str] = None  # Will be set based on price_type
    max_price: Optional[float] = None

    # Machine types configuration (unified for all providers)
    machine_types: dict[str, int] = Field(default_factory=dict)
    machine_types_ondemand: dict[str, int] = Field(default_factory=dict)
    machine_types_priority: dict[str, int] = Field(default_factory=dict)

    # Network configuration (generic concepts)
    network_zones: list[str] = Field(default_factory=list)  # subnets, zones, regions
    public_ip_assignment: Optional[bool] = None  # generic concept

    # Storage configuration (generic concepts)
    root_device_volume_size: Optional[int] = None  # root disk size
    volume_type: Optional[str] = None  # disk type
    iops: Optional[int] = None  # performance
    throughput: Optional[int] = None  # throughput
    storage_encryption: Optional[bool] = None  # encryption
    encryption_key: Optional[str] = None  # key reference

    # Access and security (generic concepts)
    key_name: Optional[str] = None  # SSH key, etc.
    user_data: Optional[str] = None  # cloud-init, etc.
    machine_role: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("machine_role", "instance_profile"),
        deprecated="use 'machine_role' instead of 'instance_profile'",
    )  # IAM role, service principal, or service account

    # Advanced configuration (extensible)
    monitoring_enabled: Optional[bool] = None

    # Tags and metadata
    tags: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    provider_data: dict[str, Any] = Field(default_factory=dict)

    # Provider configuration (multi-provider support)
    provider_type: Optional[str] = None
    provider_name: Optional[str] = None
    provider_api: Optional[str] = None

    # Timestamps for tracking
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Active status flag
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def _warn_deprecated_field_names(cls, data: Any) -> Any:
        """House pattern for operator-facing Pydantic field deprecation.

        This is the canonical way to emit operator-visible deprecation warnings
        for renamed fields in this codebase.  It runs on the raw input dict
        before Pydantic applies AliasChoices, so it fires on EVERY entry point:
        ``model_validate()``, YAML/JSON deserialization, and ``__init__`` kwargs.

        Pattern:
          1. Keep ``AliasChoices("new_name", "old_name")`` on the new field so
             old data still deserializes without a hard error.
          2. Add this ``model_validator(mode="before")`` to emit
             ``logger.warning(...)`` for each deprecated key present in the raw
             input.  The logger message appears in server logs where operators
             can see it, unlike ``warnings.warn`` which is filtered in tests and
             production by default.
          3. Mark the new field with ``Field(..., deprecated="...")`` for
             OpenAPI/JSON-schema visibility (requires Pydantic >= 2.7).
          4. Keep ``warnings.warn(DeprecationWarning)`` in ``__init__`` as a
             developer/test signal (visible via ``python -W`` or
             ``pytest.warns``).
        """
        if not isinstance(data, dict):
            return data
        if "instance_type" in data and "machine_type" not in data:
            logger.warning(
                "Template field 'instance_type' is deprecated; use 'machine_type' instead."
            )
        if "instance_profile" in data and "machine_role" not in data:
            logger.warning(
                "Template field 'instance_profile' is deprecated; use 'machine_role' instead."
            )
        return data

    def __init__(self, **data: Any) -> None:
        """Initialize template with default values and validation.

        Args:
            **data: Template configuration data

        Note:
            Sets default name from template_id if not provided.
            Sets default timestamps if not provided.
            The deprecated ``instance_type`` kwarg is accepted and mapped to
            ``machine_type`` by Pydantic's AliasChoices; a DeprecationWarning
            is emitted here as a developer-visible signal (pytest.warns / -W),
            while the operator-visible logger.warning is emitted by the
            ``_warn_deprecated_field_names`` model_validator above.

            IMPORTANT: do NOT pop the deprecated key here — popping before
            calling ``super().__init__`` would hide the key from the
            model_validator(mode="before"), preventing the logger.warning.
            AliasChoices handles the field mapping after model_validator fires.
        """
        # Emit developer-facing DeprecationWarning for deprecated kwarg names.
        # Do NOT pop the keys — leave them in data so that model_validator
        # (mode="before") can see them and emit the operator-visible logger.warning.
        # AliasChoices will map instance_type → machine_type and
        # instance_profile → machine_role during Pydantic's validation pass.
        if "instance_type" in data and "machine_type" not in data:
            warnings.warn(
                "Template field 'instance_type' is deprecated; use 'machine_type' instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        if "instance_profile" in data and "machine_role" not in data:
            warnings.warn(
                "Template field 'instance_profile' is deprecated; use 'machine_role' instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        # Set default name if not provided
        if "name" not in data and "template_id" in data:
            data["name"] = data["template_id"]

        # Set default timestamps if not provided
        if "created_at" not in data:
            data["created_at"] = datetime.now()

        if "updated_at" not in data:
            data["updated_at"] = datetime.now()

        super().__init__(**data)

    @model_validator(mode="after")
    def validate_template(self) -> "Template":
        """Validate template configuration - provider-agnostic validation only."""
        if not self.template_id:
            raise ValueError("template_id is required")

        if self.max_instances <= 0:
            raise ValueError("max_instances must be greater than 0")

        # Set allocation strategy default based on price type
        if self.allocation_strategy is None:
            if self.price_type == "spot":
                self.allocation_strategy = "priceCapacityOptimized"
            else:  # ondemand, heterogeneous
                self.allocation_strategy = "lowestPrice"

        # Reject tag keys that use the reserved system namespace
        reserved_keys = [k for k in self.tags if k.startswith("orb:")]
        if reserved_keys:
            raise ValueError(
                f"Tag keys must not start with 'orb:' (reserved for system use): "
                f"{', '.join(sorted(reserved_keys))}"
            )

        return self

    @model_validator(mode="after")
    def validate_provider_fields(self) -> "Template":
        """Validate provider field consistency following DDD principles."""
        # If provider_name is specified, extract provider_type if not provided
        if self.provider_name and not self.provider_type:
            # Extract provider type from provider name (e.g., "aws-us-east-1" -> "aws")
            if "-" in self.provider_name:
                self.provider_type = self.provider_name.split("-")[0]
            else:
                # If no separator, assume the whole name is the provider type
                self.provider_type = self.provider_name

        # Validate provider_name format if provided
        if self.provider_name:
            # Provider name should contain only alphanumeric, hyphens, and underscores
            import re

            if not re.match(r"^[a-zA-Z0-9_-]+$", self.provider_name):
                raise ValueError(
                    "provider_name must contain only alphanumeric characters, hyphens, and underscores"
                )

        # Validate provider_type format if provided
        if self.provider_type:
            # Provider type should be lowercase alphanumeric
            import re

            if not re.match(r"^[a-z0-9]+$", self.provider_type):
                raise ValueError("provider_type must be lowercase alphanumeric")

        return self

    @property
    def subnet_id(self) -> Optional[str]:
        """Convenience property for single subnet access."""
        return self.subnet_ids[0] if self.subnet_ids else None

    def update_name(self, new_name: str) -> "Template":
        """Update the name and return a new template instance."""
        return self.model_copy(update={"name": new_name})

    def update_description(self, new_description: str) -> "Template":
        """Update the description and return a new template instance."""
        return self.model_copy(update={"description": new_description})

    def update_configuration(self, configuration: dict) -> "Template":
        """Update configuration fields and return a new template instance."""
        return self.model_copy(update=configuration)

    def update_machine_type(self, new_machine_type: str) -> "Template":
        """Update the machine type and return a new template instance."""
        return self.model_copy(update={"machine_type": new_machine_type})

    def update_image_id(self, new_image_id: str) -> "Template":
        """Update the image ID and return a new template instance."""
        fields = self.model_dump(mode="json")
        fields["image_id"] = new_image_id
        fields["updated_at"] = datetime.now()
        return self.__class__.model_validate(fields)

    def add_subnet(self, subnet_id: str) -> "Template":
        """Add a subnet ID."""
        if subnet_id not in self.subnet_ids:
            new_subnets = [*self.subnet_ids, subnet_id]
            fields = self.model_dump(mode="json")
            fields["subnet_ids"] = new_subnets
            fields["updated_at"] = datetime.now()
            return self.__class__.model_validate(fields)
        return self

    def remove_subnet(self, subnet_id: str) -> "Template":
        """Remove a subnet ID."""
        if subnet_id in self.subnet_ids:
            new_subnets = [s for s in self.subnet_ids if s != subnet_id]
            fields = self.model_dump(mode="json")
            fields["subnet_ids"] = new_subnets
            fields["updated_at"] = datetime.now()
            return self.__class__.model_validate(fields)
        return self

    def add_security_group(self, security_group_id: str) -> "Template":
        """Add a security group ID."""
        if security_group_id not in self.security_group_ids:
            new_sgs = [*self.security_group_ids, security_group_id]
            fields = self.model_dump(mode="json")
            fields["security_group_ids"] = new_sgs
            fields["updated_at"] = datetime.now()
            return self.__class__.model_validate(fields)
        return self

    def remove_security_group(self, security_group_id: str) -> "Template":
        """Remove a security group ID."""
        if security_group_id in self.security_group_ids:
            new_sgs = [sg for sg in self.security_group_ids if sg != security_group_id]
            fields = self.model_dump(mode="json")
            fields["security_group_ids"] = new_sgs
            fields["updated_at"] = datetime.now()
            return self.__class__.model_validate(fields)
        return self

    def __str__(self) -> str:
        """Return string representation of template."""
        return f"Template(id={self.template_id}, provider={self.provider_api}, instances={self.max_instances})"

    def __repr__(self) -> str:
        """Detailed string representation of template."""
        return (
            f"Template(template_id='{self.template_id}', name='{self.name}', "
            f"provider_api='{self.provider_api}', max_instances={self.max_instances})"
        )


# Provider-specific template extensions should be implemented in their respective provider packages
# e.g., src/providers/aws/domain/template/aggregate.py for AWS-specific extensions

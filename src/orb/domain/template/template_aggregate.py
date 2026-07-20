"""Template configuration value object - core template domain logic."""

import logging
import warnings
from datetime import datetime
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self

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
    machine_image: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("machine_image", "image_id"),
        deprecated="use 'machine_image' instead of 'image_id'",
    )
    max_machines: int = Field(
        default=1,
        validation_alias=AliasChoices("max_machines", "max_instances"),
        deprecated="use 'max_machines' instead of 'max_instances'",
    )

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
    machine_disk_size_gb: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("machine_disk_size_gb", "root_device_volume_size"),
        deprecated="use 'machine_disk_size_gb' instead of 'root_device_volume_size'",
    )  # root disk size
    machine_disk_type: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("machine_disk_type", "volume_type"),
        deprecated="use 'machine_disk_type' instead of 'volume_type'",
    )  # disk type
    iops: Optional[int] = None  # performance
    throughput: Optional[int] = None  # throughput
    storage_encryption: Optional[bool] = None  # encryption
    encryption_key: Optional[str] = None  # key reference

    # Access and security (generic concepts)
    machine_ssh_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("machine_ssh_key", "key_name"),
        deprecated="use 'machine_ssh_key' instead of 'key_name'",
    )  # SSH key, etc.
    machine_bootstrap: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("machine_bootstrap", "user_data"),
        deprecated="use 'machine_bootstrap' instead of 'user_data'",
    )  # cloud-init, etc.
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
        if "image_id" in data and "machine_image" not in data:
            logger.warning("Template field 'image_id' is deprecated; use 'machine_image' instead.")
        if "max_instances" in data and "max_machines" not in data:
            logger.warning(
                "Template field 'max_instances' is deprecated; use 'max_machines' instead."
            )
        if "root_device_volume_size" in data and "machine_disk_size_gb" not in data:
            logger.warning(
                "Template field 'root_device_volume_size' is deprecated; "
                "use 'machine_disk_size_gb' instead."
            )
        if "volume_type" in data and "machine_disk_type" not in data:
            logger.warning(
                "Template field 'volume_type' is deprecated; use 'machine_disk_type' instead."
            )
        if "key_name" in data and "machine_ssh_key" not in data:
            logger.warning(
                "Template field 'key_name' is deprecated; use 'machine_ssh_key' instead."
            )
        if "user_data" in data and "machine_bootstrap" not in data:
            logger.warning(
                "Template field 'user_data' is deprecated; use 'machine_bootstrap' instead."
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

        if "image_id" in data and "machine_image" not in data:
            warnings.warn(
                "Template field 'image_id' is deprecated; use 'machine_image' instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        if "max_instances" in data and "max_machines" not in data:
            warnings.warn(
                "Template field 'max_instances' is deprecated; use 'max_machines' instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        if "root_device_volume_size" in data and "machine_disk_size_gb" not in data:
            warnings.warn(
                "Template field 'root_device_volume_size' is deprecated; "
                "use 'machine_disk_size_gb' instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        if "volume_type" in data and "machine_disk_type" not in data:
            warnings.warn(
                "Template field 'volume_type' is deprecated; use 'machine_disk_type' instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        if "key_name" in data and "machine_ssh_key" not in data:
            warnings.warn(
                "Template field 'key_name' is deprecated; use 'machine_ssh_key' instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        if "user_data" in data and "machine_bootstrap" not in data:
            warnings.warn(
                "Template field 'user_data' is deprecated; use 'machine_bootstrap' instead.",
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

        if self.max_machines <= 0:
            raise ValueError("max_machines must be greater than 0")

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

    # ------------------------------------------------------------------
    # Deprecated read-only compatibility accessors
    # ------------------------------------------------------------------
    # The fields above were renamed to the ``machine_*`` naming scheme, with
    # the old names kept as input-only ``AliasChoices`` so existing payloads
    # still deserialize.  These read-only properties preserve *attribute read*
    # access to the old names for the duration of the deprecation window so
    # callers doing ``template.image_id`` (etc.) keep working.  They map onto
    # the canonical field and are intentionally read-only — write via the new
    # ``machine_*`` field names.
    #
    # NOTE: ``instance_type`` -> ``machine_type`` and ``instance_profile`` ->
    # ``machine_role`` are intentionally NOT exposed as read properties.  Those
    # two fields are write-only deprecated aliases (see the deprecated-aliases
    # tests); only the six fields renamed alongside them below keep read access.

    @property
    def image_id(self) -> Optional[str]:
        """Deprecated alias for :attr:`machine_image`."""
        return self.machine_image

    @property
    def max_instances(self) -> int:
        """Deprecated alias for :attr:`max_machines`."""
        return self.max_machines

    @property
    def root_device_volume_size(self) -> Optional[int]:
        """Deprecated alias for :attr:`machine_disk_size_gb`."""
        return self.machine_disk_size_gb

    @property
    def volume_type(self) -> Optional[str]:
        """Deprecated alias for :attr:`machine_disk_type`."""
        return self.machine_disk_type

    @property
    def key_name(self) -> Optional[str]:
        """Deprecated alias for :attr:`machine_ssh_key`."""
        return self.machine_ssh_key

    @property
    def user_data(self) -> Optional[str]:
        """Deprecated alias for :attr:`machine_bootstrap`."""
        return self.machine_bootstrap

    # Maps each deprecated field name to its canonical ``machine_*`` name.  Used
    # by :meth:`model_copy` so callers passing old names in ``update=`` write the
    # canonical field rather than a silently-ignored extra attribute.
    _DEPRECATED_FIELD_NAMES: dict[str, str] = {
        "instance_type": "machine_type",
        "image_id": "machine_image",
        "max_instances": "max_machines",
        "root_device_volume_size": "machine_disk_size_gb",
        "volume_type": "machine_disk_type",
        "key_name": "machine_ssh_key",
        "user_data": "machine_bootstrap",
        "instance_profile": "machine_role",
    }

    def model_copy(self, *, update: Optional[dict[str, Any]] = None, deep: bool = False) -> Self:
        """Copy the model, translating deprecated field names in ``update``.

        The renamed fields keep read-only compatibility properties, but Pydantic
        ``model_copy`` writes ``update`` keys straight onto the instance dict.  A
        deprecated key (e.g. ``image_id``) would therefore land as an ignored
        extra attribute instead of updating the canonical field.  Fold any
        deprecated keys onto their canonical name first so old-name updates keep
        working during the deprecation window.
        """
        if update:
            translated: dict[str, Any] = {}
            for key, value in update.items():
                canonical = self._DEPRECATED_FIELD_NAMES.get(key, key)
                # An explicit canonical key in the same update dict wins.
                if canonical in update and canonical != key:
                    continue
                translated[canonical] = value
            update = translated
        return super().model_copy(update=update, deep=deep)

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
        fields["machine_image"] = new_image_id
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
        return f"Template(id={self.template_id}, provider={self.provider_api}, instances={self.max_machines})"

    def __repr__(self) -> str:
        """Detailed string representation of template."""
        return (
            f"Template(template_id='{self.template_id}', name='{self.name}', "
            f"provider_api='{self.provider_api}', max_machines={self.max_machines})"
        )


# Provider-specific template extensions should be implemented in their respective provider packages
# e.g., src/providers/aws/domain/template/aggregate.py for AWS-specific extensions

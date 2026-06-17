"""Azure template extension configuration."""

from typing import Any, Optional

from pydantic import BaseModel, Field

class AzureTemplateExtensionConfig(BaseModel):
    """Azure-specific template extension defaults.

    Registered with ``TemplateExtensionRegistry`` so the template factory
    can apply Azure defaults when ``provider_type == "azure"``.
    """

    # VM configuration
    vm_size: str = Field(
        "Standard_D4s_v5",
        description="Default Azure VM size",
    )
    vm_sizes: Optional[list[str]] = Field(
        default=None,
        description="Additional Azure VM size candidates for generic instance mix",
    )
    vm_size_preferences: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Ranked Azure VM size candidates for Prioritized VMSS instance mix",
    )
    vmss_allocation_strategy: Optional[str] = Field(
        default=None,
        description="Azure VMSS instance-mix allocation strategy",
    )

    # Pricing
    priority: str = Field("Regular", description="Default VM priority")

    # OS disk
    os_disk_type: str = Field("Premium_LRS", description="Default OS disk type")
    os_disk_size_gb: Optional[int] = Field(
        None, description="OS disk size in GiB (None = image default)"
    )

    # Identity
    admin_username: str = Field("azureuser", description="Default admin username")

    # Freeform attributes
    node_attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Freeform attributes merged into the template",
    )

    def to_template_defaults(self) -> dict[str, Any]:
        """Convert extension to a dict of default values for template creation."""
        defaults: dict[str, Any] = {
            "vm_size": self.vm_size,
            "priority": self.priority,
            "admin_username": self.admin_username,
            "node_attributes": self.node_attributes,
        }
        if self.vm_sizes:
            defaults["vm_sizes"] = self.vm_sizes
        if self.vm_size_preferences:
            defaults["vm_size_preferences"] = self.vm_size_preferences
        if self.vmss_allocation_strategy:
            defaults["vmss_allocation_strategy"] = self.vmss_allocation_strategy
        if self.os_disk_size_gb is not None:
            defaults["os_disk"] = {
                "storage_account_type": self.os_disk_type,
                "disk_size_gb": self.os_disk_size_gb,
            }
        return defaults

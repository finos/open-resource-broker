"""Azure configuration and template validation utilities."""

from typing import Any

from providers.azure.configuration.config import AzureProviderConfig
from providers.azure.domain.template.value_objects import AzureProviderApi


def validate_azure_config(config: AzureProviderConfig) -> dict[str, Any]:
    """Validate an AzureProviderConfig and return a structured result.

    Returns:
        dict with keys: valid (bool), errors (list[str]), warnings (list[str])
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not config.subscription_id:
        errors.append("subscription_id is required for Azure operations")
    if not config.resource_group:
        warnings.append("resource_group not set; templates must specify it explicitly")
    if not config.region:
        warnings.append("region not set; defaulting to 'eastus2'")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def validate_azure_template(template_config: dict[str, Any]) -> dict[str, Any]:
    """Validate an Azure template configuration dict.

    Performs Azure-specific field checks without constructing the full
    ``AzureTemplate`` aggregate (which has its own Pydantic validators).

    Returns:
        dict with keys: valid, errors, warnings, validated_fields
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Required fields --------------------------------------------------
    if "vm_size" not in template_config:
        errors.append("Missing required field: vm_size")

    if "resource_group" not in template_config:
        errors.append("Missing required field: resource_group")

    if "location" not in template_config:
        errors.append("Missing required field: location")

    # Image must be specified via image dict or core image_id
    has_image = bool(template_config.get("image"))
    has_image_id = bool(template_config.get("image_id"))
    if not has_image and not has_image_id:
        errors.append(
            "Missing image configuration: provide 'image' (publisher/offer/sku or image_id) "
            "or the core 'image_id' field"
        )

    # VM size validation -----------------------------------------------
    vm_size = template_config.get("vm_size", "")
    if vm_size and not vm_size.startswith("Standard_"):
        warnings.append(
            f"Uncommon VM size format: '{vm_size}'. "
            "Azure VM sizes typically start with 'Standard_'."
        )

    # Provider API validation ------------------------------------------
    provider_api = template_config.get("provider_api", "VMSS")
    valid_apis = {e.value for e in AzureProviderApi}
    if provider_api not in valid_apis:
        errors.append(
            f"Invalid provider_api '{provider_api}'. Must be one of: {sorted(valid_apis)}"
        )

    # Networking -------------------------------------------------------
    network_config = template_config.get("network_config")
    if network_config and isinstance(network_config, dict):
        if "subnet_id" not in network_config:
            errors.append("network_config.subnet_id is required when network_config is provided")

    # Spot validation --------------------------------------------------
    priority = template_config.get("priority", "Regular")
    eviction_policy = template_config.get("eviction_policy")
    billing_max_price = template_config.get("billing_profile_max_price")
    spot_percentage = template_config.get("spot_percentage")
    orchestration_mode = template_config.get("orchestration_mode", "Flexible")
    single_placement_group = bool(template_config.get("single_placement_group", False))

    if priority == "Regular":
        if eviction_policy is not None:
            errors.append("eviction_policy is only valid for Spot or Low priority VMs")
        if billing_max_price is not None:
            errors.append("billing_profile_max_price is only valid for Spot priority VMs")

    if spot_percentage is not None:
        if not isinstance(spot_percentage, int) or not (0 <= spot_percentage <= 100):
            errors.append("spot_percentage must be an integer between 0 and 100")
        if provider_api not in {"VMSS", "VMSSUniform"}:
            errors.append("spot_percentage is only supported for VMSS-based templates")
        if orchestration_mode != "Flexible":
            errors.append("spot_percentage requires Flexible orchestration mode")
        if single_placement_group:
            errors.append(
                "spot_percentage is not supported when single_placement_group is enabled"
            )
        if priority == "Low":
            errors.append("spot_percentage is not compatible with Low priority VMs")

    # Zone balance requires zones
    if template_config.get("zone_balance") and not template_config.get("zones"):
        errors.append("zone_balance requires at least one availability zone")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "validated_fields": list(template_config.keys()),
    }

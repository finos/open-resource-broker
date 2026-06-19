"""Azure configuration and template validation utilities."""

from typing import Any

from pydantic import ValidationError

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate


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
    """Validate an Azure template configuration dict via ``AzureTemplate``.

    ``AzureTemplate`` is the authoritative validation path for Azure template
    configuration. This helper exists only to normalize model-validation
    failures into the validation result shape used by the strategy and
    validation adapter.
    """
    warnings: list[str] = []

    vm_size = template_config.get("vm_size", "")
    if vm_size and not vm_size.startswith("Standard_"):
        warnings.append(
            f"Uncommon VM size format: '{vm_size}'. "
            "Azure VM sizes typically start with 'Standard_'."
        )

    try:
        AzureTemplate.model_validate(template_config)
        errors: list[str] = []
    except ValidationError as exc:
        errors = []
        for issue in exc.errors():
            message = issue.get("msg", "Validation error")
            location = ".".join(str(part) for part in issue.get("loc", []))
            errors.append(f"{location}: {message}" if location else message)
    except Exception as exc:
        errors = [str(exc)]

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "validated_fields": list(template_config.keys()),
    }

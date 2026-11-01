"""Per-request template overrides.

Callers may adjust a small, explicit set of template fields for a single
request without editing the stored template. Everything outside the allowlist
is rejected, including identity, credential and bootstrap fields.
"""

from __future__ import annotations

from typing import Any

from orb.application.dto.template import TemplateDTO
from orb.domain.request.exceptions import RequestValidationError

# Fields a caller may override for a single request.
OVERRIDABLE_FIELDS: frozenset[str] = frozenset(
    {
        "subnet_ids",
        "security_group_ids",
        "machine_types",
        "machine_types_ondemand",
        "machine_types_priority",
        "price_type",
        "max_price",
        "network_zones",
        "public_ip_assignment",
        "tags",
    }
)

# Alternate spellings accepted by the template model, mapped to the canonical
# name. They are resolved before the allowlist check so an alias can never
# reach a field the canonical name could not.
_ALIASES: dict[str, str] = {
    "instance_type": "machine_type",
    "image_id": "machine_image",
    "max_instances": "max_machines",
    "root_device_volume_size": "machine_disk_size_gb",
    "volume_type": "machine_disk_type",
    "key_name": "machine_ssh_key",
    "user_data": "machine_bootstrap",
    "instance_profile": "machine_role",
}

# Fields that identify the template or carry credentials, identity or boot code.
# They are never overridable; this set only sharpens the error message.
_PROTECTED_FIELDS: frozenset[str] = frozenset(
    {
        "template_id",
        "provider_type",
        "provider_name",
        "provider_api",
        "provider_config",
        "provider_data",
        "machine_role",
        "machine_bootstrap",
        "machine_ssh_key",
        "encryption_key",
    }
)

_RESERVED_TAG_PREFIX = "orb:"


def normalize_override_keys(overrides: dict[str, Any]) -> dict[str, Any]:
    """Resolve aliases to canonical names, rejecting conflicting duplicates."""
    normalized: dict[str, Any] = {}
    for key, value in overrides.items():
        canonical = _ALIASES.get(key, key)
        if canonical in normalized:
            raise RequestValidationError(
                f"Template override '{key}' duplicates another override for '{canonical}'"
            )
        normalized[canonical] = value
    return normalized


def apply_template_overrides(template: Any, overrides: dict[str, Any]) -> Any:
    """Return a copy of ``template`` with ``overrides`` applied and re-validated.

    Raises:
        RequestValidationError: for unknown or protected keys and invalid values.
    """
    if not overrides:
        return template
    if not isinstance(template, TemplateDTO):
        raise RequestValidationError("Template overrides are not supported for this template type")

    normalized = normalize_override_keys(overrides)

    protected = sorted(k for k in normalized if k in _PROTECTED_FIELDS)
    if protected:
        raise RequestValidationError(f"Template overrides cannot change: {', '.join(protected)}")
    unknown = sorted(k for k in normalized if k not in OVERRIDABLE_FIELDS)
    if unknown:
        raise RequestValidationError(
            f"Unsupported template overrides: {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(OVERRIDABLE_FIELDS))}"
        )

    merged = dict(normalized)
    if "tags" in merged:
        tags = merged["tags"]
        if not isinstance(tags, dict):
            raise RequestValidationError("Template override 'tags' must be a mapping")
        reserved = sorted(str(k) for k in tags if str(k).startswith(_RESERVED_TAG_PREFIX))
        if reserved:
            raise RequestValidationError(
                f"Template override tags may not use the reserved '{_RESERVED_TAG_PREFIX}' "
                f"prefix: {', '.join(reserved)}"
            )
        merged["tags"] = {**template.tags, **tags}

    # Validate against the concrete DTO class, keeping the typed provider_config.
    data = {name: getattr(template, name) for name in type(template).model_fields}
    data.update(merged)
    try:
        return type(template).model_validate(data)
    except Exception as exc:
        raise RequestValidationError(f"Invalid template override: {exc}") from exc

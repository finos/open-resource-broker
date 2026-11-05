"""Snapshot of the template a request was provisioned with.

The resolved template is recorded on the request so later operations (return,
terminate) act on the template as it was at provisioning time, even when the
stored template has since changed, was never stored, or the process restarted.
"""

from __future__ import annotations

from typing import Any, Optional

from orb.application.dto.template import TemplateDTO

SNAPSHOT_METADATA_KEY = "template_snapshot"

# Only what teardown needs to address the resources a request created. Role,
# key, bootstrap, encryption and provider-specific configuration are left out:
# the snapshot is stored in request metadata, which status output can expose.
SNAPSHOT_FIELDS: tuple[str, ...] = (
    "template_id",
    "name",
    "provider_type",
    "provider_name",
    "provider_api",
    "price_type",
    "subnet_ids",
    "security_group_ids",
    "machine_types",
    "machine_types_ondemand",
    "machine_types_priority",
    "network_zones",
    "tags",
)


def capture_template_snapshot(template: Any) -> Optional[dict[str, Any]]:
    """Serialise a resolved template for storage on the request.

    Only ``TemplateDTO`` instances are captured; anything else returns ``None``.
    """
    if not isinstance(template, TemplateDTO):
        return None
    dumped = template.model_dump(mode="json", include=set(SNAPSHOT_FIELDS))
    return {name: dumped[name] for name in SNAPSHOT_FIELDS if name in dumped}


def restore_template_snapshot(
    snapshot: Optional[dict[str, Any]], template_id: Optional[str] = None
) -> Optional[TemplateDTO]:
    """Rebuild a template from a stored snapshot, or ``None`` if unusable."""
    if not snapshot or not isinstance(snapshot, dict):
        return None
    data = dict(snapshot)
    if template_id and data.get("template_id") != template_id:
        return None
    try:
        return TemplateDTO.model_validate({k: v for k, v in data.items() if k in SNAPSHOT_FIELDS})
    except Exception:
        return None

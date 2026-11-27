"""Placeholder instances reported for returned machines whose pods could not be read."""

from __future__ import annotations

from typing import Any, Optional


def unreadable_placeholders(
    machine_ids: list[str],
    machine_coordinates: Optional[dict[str, dict[str, Any]]],
    instance_type: str,
    status: str = "unknown",
) -> list[dict[str, Any]]:
    """One placeholder per machine whose pods could not be listed.

    A machine the status sync is never told about is read as gone and closed
    out, so each unreadable machine is reported with a status that changes
    nothing. The sync overlays whatever a read reports onto the stored row,
    so the stored placement is carried back; leaving it out would send the
    next read to the default namespace. Addresses and tags are not carried:
    they are refreshed by the next successful read.
    """
    return [
        {
            "instance_id": mid,
            "resource_id": mid,
            "instance_type": instance_type,
            "image_id": "unknown",
            "launch_time": None,
            "status": status,
            "provider_data": dict(
                ((machine_coordinates or {}).get(mid) or {}).get("provider_data") or {}
            ),
        }
        for mid in machine_ids
    ]

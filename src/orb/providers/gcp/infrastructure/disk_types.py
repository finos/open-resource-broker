"""Helpers for normalizing GCP boot disk type references."""

from __future__ import annotations

from typing import Literal


DiskTypePayloadContext = Literal["instance", "instance_template"]


def normalize_boot_disk_type(
    disk_type: str,
    *,
    zone: str | None,
    payload_context: DiskTypePayloadContext,
) -> str:
    """Return a stable boot disk type reference for Compute Engine payloads.

    Standalone instance inserts accept zonal disk type references. Global
    instance templates require only the disk type resource name.
    """
    if payload_context == "instance_template":
        if "/" in disk_type:
            raise ValueError("instance template disk_type must be a disk type resource name")
        return disk_type
    if "/" in disk_type:
        return disk_type
    if zone:
        return f"zones/{zone}/diskTypes/{disk_type}"
    return disk_type

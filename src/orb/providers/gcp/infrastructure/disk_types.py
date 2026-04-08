"""Helpers for normalizing GCP boot disk type references."""

from __future__ import annotations


def normalize_boot_disk_type(disk_type: str, *, zone: str | None) -> str:
    """Return a stable boot disk type reference for Compute Engine payloads.

    Compute Engine accepts fully qualified disk type references. When a caller
    provides a short disk type name and the target zone is known, expand it to
    the zonal diskTypes path so SingleVM and MIG payload builders behave the
    same way. If no zone is available yet, preserve the short name rather than
    inventing one.
    """
    if "/" in disk_type:
        return disk_type
    if zone:
        return f"zones/{zone}/diskTypes/{disk_type}"
    return disk_type

"""Tests for GCP boot disk type normalization."""

from __future__ import annotations

from orb.providers.gcp.infrastructure.disk_types import normalize_boot_disk_type


def test_short_disk_type_expands_to_zonal_reference_when_zone_known() -> None:
    assert normalize_boot_disk_type("pd-balanced", zone="us-central1-a") == (
        "zones/us-central1-a/diskTypes/pd-balanced"
    )


def test_full_disk_type_reference_is_preserved() -> None:
    disk_type = "zones/us-central1-b/diskTypes/pd-ssd"

    assert normalize_boot_disk_type(disk_type, zone="us-central1-a") == disk_type


def test_short_disk_type_stays_short_when_zone_unknown() -> None:
    assert normalize_boot_disk_type("pd-balanced", zone=None) == "pd-balanced"

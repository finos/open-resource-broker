"""Shared constants for the GCP provider."""

from __future__ import annotations


DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/cloud-platform",
)

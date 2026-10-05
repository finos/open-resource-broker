"""Shared constants for the GCP provider."""

from __future__ import annotations

# Applied when a template attaches a service account (``service_account_email``
# is set) without specifying its own ``service_account_scopes``. This mirrors
# the "Allow default access" scope set Google Cloud Console/gcloud grant the
# default Compute Engine service account, rather than the broader
# ``https://www.googleapis.com/auth/compute`` scope, which grants read/write
# access to every Compute Engine resource in the project.
# https://cloud.google.com/compute/docs/access/service-accounts#default_service_account_and_scopes
DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/devstorage.read_only",
    "https://www.googleapis.com/auth/logging.write",
    "https://www.googleapis.com/auth/monitoring.write",
    "https://www.googleapis.com/auth/service.management.readonly",
    "https://www.googleapis.com/auth/servicecontrol",
    "https://www.googleapis.com/auth/trace.append",
)

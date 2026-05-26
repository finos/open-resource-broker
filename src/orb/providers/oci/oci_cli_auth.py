"""Helpers for OCI CLI authentication flags used by ORB subprocess calls."""

from __future__ import annotations

import os

# Values accepted by `oci --auth` (subset used by ORB).
CLI_AUTH_MODES = frozenset(
    {
        "api_key",
        "instance_principal",
        "resource_principal",
        "security_token",
        "instance_obo_user",
        "oke_workload_identity",
    }
)

PRINCIPAL_AUTH_MODES = frozenset({"instance_principal", "resource_principal"})


def build_oci_cli_extra_args(
    *,
    profile: str | None = None,
    credential_source: str | None = None,
) -> list[str]:
    """Build extra OCI CLI arguments for authentication.

    Precedence:
    1. ``credential_source`` from ORB provider config (or ``ORB_OCI_CREDENTIAL_SOURCE``)
    2. ``OCI_CLI_AUTH`` environment variable
    3. ``--profile`` when a config profile is set

    Principal-based auth never passes ``--profile`` (API key file not required).
    """
    source = (credential_source or os.environ.get("ORB_OCI_CREDENTIAL_SOURCE") or "").strip().lower()
    if source in CLI_AUTH_MODES:
        return ["--auth", source]

    cli_auth = os.environ.get("OCI_CLI_AUTH", "").strip().lower()
    if cli_auth in CLI_AUTH_MODES:
        return ["--auth", cli_auth]

    if profile:
        return ["--profile", profile]

    return []

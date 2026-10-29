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
PROFILE_AUTH_MODES = frozenset({"profile", "default"})


def build_oci_cli_extra_args(
    *,
    profile: str | None = None,
    credential_source: str | None = None,
) -> list[str]:
    """Build extra OCI CLI arguments for authentication.

    Precedence:
    1. ``credential_source`` from ORB provider config
    2. ``ORB_OCI_CREDENTIAL_SOURCE`` environment variable
    3. ``OCI_CLI_AUTH`` environment variable
    4. ``--profile`` when a config profile is set

    Principal-based auth never passes ``--profile`` (API key file not required).
    """
    source = (
        (credential_source or os.environ.get("ORB_OCI_CREDENTIAL_SOURCE") or "").strip().lower()
    )
    if source in CLI_AUTH_MODES:
        args = ["--auth", source]
        if profile and source not in PRINCIPAL_AUTH_MODES:
            args.extend(["--profile", profile])
        return args
    if source in PROFILE_AUTH_MODES:
        return ["--profile", profile] if profile else []

    cli_auth = os.environ.get("OCI_CLI_AUTH", "").strip().lower()
    if cli_auth in CLI_AUTH_MODES:
        args = ["--auth", cli_auth]
        if profile and cli_auth not in PRINCIPAL_AUTH_MODES:
            args.extend(["--profile", profile])
        return args

    if profile:
        return ["--profile", profile]

    return []


def build_oci_cli_env(config: dict) -> dict[str, str]:
    """Pass explicit API-key configuration only to the child OCI process.

    OCI CLI documents these variables as overrides for config-file entries.
    Values are never added to command arguments or the parent environment.
    """
    env = dict(os.environ)
    for field, variable in {
        "tenancy_ocid": "OCI_CLI_TENANCY",
        "user_ocid": "OCI_CLI_USER",
        "fingerprint": "OCI_CLI_FINGERPRINT",
        "private_key_path": "OCI_CLI_KEY_FILE",
    }.items():
        value = config.get(field)
        if value:
            env[variable] = str(value)
    return env

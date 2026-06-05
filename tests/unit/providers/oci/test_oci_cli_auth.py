"""Unit tests for OCI CLI auth argument builder."""

import os
from unittest.mock import patch

from orb.providers.oci.oci_cli_auth import build_oci_cli_extra_args


def test_instance_principal_from_config() -> None:
    args = build_oci_cli_extra_args(
        profile="DEFAULT",
        credential_source="instance_principal",
    )
    assert args == ["--auth", "instance_principal"]


def test_profile_when_no_principal_source(monkeypatch) -> None:
    monkeypatch.delenv("OCI_CLI_AUTH", raising=False)
    args = build_oci_cli_extra_args(profile="DEFAULT", credential_source=None)
    assert args == ["--profile", "DEFAULT"]


def test_config_profile_source_beats_oci_cli_auth_env() -> None:
    with patch.dict(os.environ, {"OCI_CLI_AUTH": "instance_principal"}, clear=False):
        args = build_oci_cli_extra_args(profile="DEFAULT", credential_source="profile")
    assert args == ["--profile", "DEFAULT"]


def test_oci_cli_auth_env_fallback() -> None:
    with patch.dict(os.environ, {"OCI_CLI_AUTH": "instance_principal"}, clear=False):
        args = build_oci_cli_extra_args(profile="DEFAULT", credential_source=None)
    assert args == ["--auth", "instance_principal"]


def test_config_source_beats_env_profile() -> None:
    with patch.dict(os.environ, {"OCI_CLI_AUTH": "api_key"}, clear=False):
        args = build_oci_cli_extra_args(
            profile="DEFAULT",
            credential_source="resource_principal",
        )
    assert args == ["--auth", "resource_principal"]

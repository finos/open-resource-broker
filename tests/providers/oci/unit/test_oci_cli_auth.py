"""Unit tests for OCI CLI auth argument builder."""

import os
from unittest.mock import patch

from orb.providers.oci.oci_cli_auth import build_oci_cli_extra_args


def test_subprocess_resolves_windows_cli_wrapper(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler

    wrapper = r"C:\Users\test\bin\oci.CMD"
    monkeypatch.setattr(
        "orb.providers.oci.handlers.oci_compute_handler.shutil.which", lambda _: wrapper
    )
    run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout='{"data": []}'))
    monkeypatch.setattr("orb.providers.oci.handlers.oci_compute_handler.subprocess.run", run)
    handler = OCIComputeHandler(
        MagicMock(), "us-phoenix-1", profile="BENCHMARK", credential_source="security_token"
    )
    assert handler._run_oci(["iam", "availability-domain", "list"]) == {"data": []}
    assert run.call_args.args[0][0] == wrapper
    assert run.call_args.args[0][-4:] == ["--auth", "security_token", "--profile", "BENCHMARK"]


def test_security_token_preserves_selected_profile() -> None:
    args = build_oci_cli_extra_args(profile="BENCHMARK", credential_source="security_token")
    assert args == ["--auth", "security_token", "--profile", "BENCHMARK"]


def test_env_security_token_preserves_selected_profile(monkeypatch) -> None:
    monkeypatch.delenv("ORB_OCI_CREDENTIAL_SOURCE", raising=False)
    monkeypatch.setenv("OCI_CLI_AUTH", "security_token")
    args = build_oci_cli_extra_args(profile="BENCHMARK")
    assert args == ["--auth", "security_token", "--profile", "BENCHMARK"]


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

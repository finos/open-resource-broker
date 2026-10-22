"""Unit tests for OCI auth/config validation behavior."""

from unittest.mock import MagicMock

import pytest

from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy


@pytest.mark.parametrize("value", ["~/oci-key.pem", "$OCI_TEST_KEY_DIR/oci-key.pem", "oci-key.pem"])
def test_api_key_path_expands_user_environment_and_relative_paths(tmp_path, monkeypatch, value):
    import os

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("OCI_TEST_KEY_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    key = tmp_path / "oci-key.pem"
    key.write_text("synthetic key fixture")
    config = OCIProviderConfig(
        credential_source="api_key",
        tenancy_ocid="tenancy",
        user_ocid="user",
        fingerprint="fingerprint",
        private_key_path=value,
    )
    assert config.private_key_path == os.path.normpath(str(key))
    assert config.validate_auth_configuration()[0]


@pytest.mark.parametrize("directory", [True, False])
def test_api_key_path_requires_existing_file(tmp_path, directory):
    key = tmp_path if directory else tmp_path / "missing.pem"
    result = OCIProviderStrategy.test_credentials(
        "api_key",
        tenancy_ocid="tenancy",
        user_ocid="user",
        fingerprint="fingerprint",
        private_key_path=str(key),
    )
    assert not result["success"]
    assert result["missing_fields"] == ["private_key_path"]


def test_session_auth_accepts_named_profile_without_api_key_fields() -> None:
    config = OCIProviderConfig(credential_source="security_token", profile="BENCHMARK")
    valid, _, missing, source = config.validate_auth_configuration()
    assert valid
    assert missing == []
    assert source == "security_token"


def test_session_auth_requires_profile() -> None:
    config = OCIProviderConfig(credential_source="security_token")
    valid, _, missing, _ = config.validate_auth_configuration()
    assert not valid
    assert missing == ["profile"]


def test_config_validation_succeeds_with_profile() -> None:
    config = OCIProviderConfig(region="us-phoenix-1", profile="DEFAULT")

    is_valid, message, missing, source = config.validate_auth_configuration()

    assert is_valid is True
    assert source == "profile"
    assert missing == []
    assert "valid" in message.lower()


def test_config_validation_succeeds_with_default_oci_cli_credentials() -> None:
    config = OCIProviderConfig(region="us-phoenix-1")

    is_valid, message, missing, source = config.validate_auth_configuration()

    assert is_valid is True
    assert source == "default"
    assert missing == []
    assert "default oci cli" in message.lower()


def test_config_validation_succeeds_for_api_key_source_when_complete(tmp_path) -> None:
    config = OCIProviderConfig(region="us-phoenix-1")
    key = tmp_path / "key.pem"
    key.write_text("synthetic key fixture")

    is_valid, _, missing, source = config.validate_auth_configuration(
        credential_source="api_key",
        tenancy_ocid="ocid1.tenancy.oc1..abc",
        user_ocid="ocid1.user.oc1..abc",
        fingerprint="aa:bb:cc",
        private_key_path=str(key),
    )

    assert is_valid is True
    assert source == "api_key"
    assert missing == []


def test_strategy_test_credentials_reports_missing_fields() -> None:
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1"), logger=MagicMock()
    )

    result = strategy.test_credentials(credential_source="api_key")

    assert result["success"] is False
    assert result["source"] == "api_key"
    assert "tenancy_ocid" in result["missing_fields"]


def test_strategy_test_credentials_rejects_unsupported_source() -> None:
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1"), logger=MagicMock()
    )

    result = strategy.test_credentials(credential_source="foo")

    assert result["success"] is False
    assert result["source"] == "foo"
    assert "unsupported credential source" in result["message"].lower()


def test_strategy_health_healthy_with_default_oci_cli_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        "orb.providers.oci.strategy.oci_provider_strategy.run_oci",
        lambda *args, **kwargs: {"data": "namespace"},
    )
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1"), logger=MagicMock()
    )
    strategy.initialize()

    health = strategy.check_health()

    assert health.is_healthy is True
    assert "authenticated" in health.status_message


def test_config_validation_succeeds_for_instance_principal_source() -> None:
    config = OCIProviderConfig(region="us-phoenix-1", credential_source="instance_principal")

    is_valid, message, missing, source = config.validate_auth_configuration()

    assert is_valid is True
    assert source == "instance_principal"
    assert missing == []
    assert "principal" in message.lower()


def test_strategy_health_healthy_with_instance_principal_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "orb.providers.oci.strategy.oci_provider_strategy.run_oci",
        lambda *args, **kwargs: {"data": "namespace"},
    )
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", credential_source="instance_principal"),
        logger=MagicMock(),
    )
    strategy.initialize()

    health = strategy.check_health()

    assert health.is_healthy is True
    assert "authenticated" in health.status_message


def test_strategy_health_healthy_with_profile_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "orb.providers.oci.strategy.oci_provider_strategy.run_oci",
        lambda *args, **kwargs: {"data": "namespace"},
    )
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", profile="DEFAULT"),
        logger=MagicMock(),
    )
    strategy.initialize()

    health = strategy.check_health()

    assert health.is_healthy is True
    assert "authenticated" in health.status_message

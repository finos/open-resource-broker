"""Unit tests for OCI auth/config validation behavior."""

from unittest.mock import MagicMock

from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy


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


def test_config_validation_succeeds_for_api_key_source_when_complete() -> None:
    config = OCIProviderConfig(region="us-phoenix-1")

    is_valid, _, missing, source = config.validate_auth_configuration(
        credential_source="api_key",
        tenancy_ocid="ocid1.tenancy.oc1..abc",
        user_ocid="ocid1.user.oc1..abc",
        fingerprint="aa:bb:cc",
        private_key_path="/tmp/key.pem",
    )

    assert is_valid is True
    assert source == "api_key"
    assert missing == []


def test_strategy_test_credentials_reports_missing_fields() -> None:
    strategy = OCIProviderStrategy(config=OCIProviderConfig(region="us-phoenix-1"), logger=MagicMock())

    result = strategy.test_credentials(credential_source="api_key")

    assert result["success"] is False
    assert result["source"] == "api_key"
    assert "tenancy_ocid" in result["missing_fields"]


def test_strategy_test_credentials_rejects_unsupported_source() -> None:
    strategy = OCIProviderStrategy(config=OCIProviderConfig(region="us-phoenix-1"), logger=MagicMock())

    result = strategy.test_credentials(credential_source="foo")

    assert result["success"] is False
    assert result["source"] == "foo"
    assert "unsupported credential source" in result["message"].lower()


def test_strategy_health_healthy_with_default_oci_cli_credentials() -> None:
    strategy = OCIProviderStrategy(config=OCIProviderConfig(region="us-phoenix-1"), logger=MagicMock())
    strategy.initialize()

    health = strategy.check_health()

    assert health.is_healthy is True
    assert "auth_source=default" in health.status_message


def test_config_validation_succeeds_for_instance_principal_source() -> None:
    config = OCIProviderConfig(region="us-phoenix-1", credential_source="instance_principal")

    is_valid, message, missing, source = config.validate_auth_configuration()

    assert is_valid is True
    assert source == "instance_principal"
    assert missing == []
    assert "principal" in message.lower()


def test_strategy_health_healthy_with_instance_principal_auth() -> None:
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", credential_source="instance_principal"),
        logger=MagicMock(),
    )
    strategy.initialize()

    health = strategy.check_health()

    assert health.is_healthy is True
    assert "auth_source=instance_principal" in health.status_message


def test_strategy_health_healthy_with_profile_auth() -> None:
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", profile="DEFAULT"),
        logger=MagicMock(),
    )
    strategy.initialize()

    health = strategy.check_health()

    assert health.is_healthy is True
    assert "auth_source=profile" in health.status_message

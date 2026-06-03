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


def test_config_validation_fails_when_missing_profile_and_api_key_fields() -> None:
    config = OCIProviderConfig(region="us-phoenix-1")

    is_valid, message, missing, source = config.validate_auth_configuration()

    assert is_valid is False
    assert source == "default"
    assert "tenancy_ocid" in missing
    assert "profile" not in missing
    assert "no usable oci auth" in message.lower()


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


def test_strategy_health_unhealthy_when_auth_is_invalid() -> None:
    strategy = OCIProviderStrategy(config=OCIProviderConfig(region="us-phoenix-1"), logger=MagicMock())
    strategy.initialize()

    health = strategy.check_health()

    assert health.is_healthy is False
    assert "auth configuration invalid" in health.status_message.lower()


def test_strategy_health_healthy_with_profile_auth() -> None:
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", profile="DEFAULT"),
        logger=MagicMock(),
    )
    strategy.initialize()

    health = strategy.check_health()

    assert health.is_healthy is True
    assert "auth_source=profile" in health.status_message

"""Unit tests for AWSClient helper functions.

Tests _is_sensitive_key and _mask_config_dict — the two module-level
pure functions that don't require AWS credentials.
These are fast, pure-Python tests with no I/O.
"""

import pytest

from orb.providers.aws.infrastructure.aws_client import _is_sensitive_key, _mask_config_dict

# ---------------------------------------------------------------------------
# _is_sensitive_key
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsSensitiveKey:
    # Exact-match known-secret keys
    def test_access_key_id_is_sensitive(self):
        assert _is_sensitive_key("access_key_id") is True

    def test_secret_access_key_is_sensitive(self):
        assert _is_sensitive_key("secret_access_key") is True

    def test_session_token_is_sensitive(self):
        assert _is_sensitive_key("session_token") is True

    # Case-insensitive exact match
    def test_access_key_id_uppercase_is_sensitive(self):
        assert _is_sensitive_key("ACCESS_KEY_ID") is True

    # Suffix matching
    def test_key_ending_with_access_key_id_is_sensitive(self):
        assert _is_sensitive_key("aws_access_key_id") is True

    def test_key_ending_with_secret_access_key_is_sensitive(self):
        assert _is_sensitive_key("my_secret_access_key") is True

    # Fragment matching
    def test_key_containing_secret_is_sensitive(self):
        assert _is_sensitive_key("my_secret_value") is True

    def test_key_containing_password_is_sensitive(self):
        assert _is_sensitive_key("db_password") is True

    def test_key_containing_token_is_sensitive(self):
        assert _is_sensitive_key("auth_token") is True

    def test_key_containing_credential_is_sensitive(self):
        assert _is_sensitive_key("aws_credential") is True

    # Non-sensitive keys
    def test_region_is_not_sensitive(self):
        assert _is_sensitive_key("region") is False

    def test_key_name_alone_is_not_sensitive(self):
        """bare 'key' should NOT be flagged — avoids hiding debug fields"""
        assert _is_sensitive_key("key") is False

    def test_key_file_is_not_sensitive(self):
        assert _is_sensitive_key("key_file") is False

    def test_public_key_is_not_sensitive(self):
        assert _is_sensitive_key("public_key") is False

    def test_profile_is_not_sensitive(self):
        assert _is_sensitive_key("profile") is False

    def test_table_name_is_not_sensitive(self):
        assert _is_sensitive_key("table_name") is False


# ---------------------------------------------------------------------------
# _mask_config_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMaskConfigDict:
    def test_scalar_non_sensitive_passed_through(self):
        config = {"region": "us-east-1"}
        masked = _mask_config_dict(config)
        assert masked["region"] == "us-east-1"

    def test_scalar_sensitive_replaced(self):
        config = {"secret_access_key": "real-secret"}
        masked = _mask_config_dict(config)
        assert masked["secret_access_key"] == "***"

    def test_nested_dict_sensitive_replaced(self):
        config = {"auth": {"secret_access_key": "real-secret", "region": "us-east-1"}}
        masked = _mask_config_dict(config)
        assert masked["auth"]["secret_access_key"] == "***"
        assert masked["auth"]["region"] == "us-east-1"

    def test_list_with_dict_items_recursed(self):
        config = {
            "providers": [
                {"name": "p1", "access_key_id": "AKID123"},
                {"name": "p2"},
            ]
        }
        masked = _mask_config_dict(config)
        assert masked["providers"][0]["access_key_id"] == "***"
        assert masked["providers"][0]["name"] == "p1"
        assert masked["providers"][1]["name"] == "p2"

    def test_list_with_scalar_items_passed_through(self):
        config = {"subnets": ["subnet-001", "subnet-002"]}
        masked = _mask_config_dict(config)
        assert masked["subnets"] == ["subnet-001", "subnet-002"]

    def test_deep_nesting_still_masked(self):
        config = {"level1": {"level2": {"session_token": "tok-123"}}}
        masked = _mask_config_dict(config)
        assert masked["level1"]["level2"]["session_token"] == "***"

    def test_empty_dict_returns_empty(self):
        assert _mask_config_dict({}) == {}

    def test_original_dict_not_mutated(self):
        config = {"secret_access_key": "real-secret", "region": "us-east-1"}
        _mask_config_dict(config)
        # original is unchanged
        assert config["secret_access_key"] == "real-secret"

    def test_password_in_key_masked(self):
        config = {"db_password": "s3cr3t"}
        masked = _mask_config_dict(config)
        assert masked["db_password"] == "***"

    def test_none_value_not_affected_by_sensitive_key(self):
        config = {"secret_access_key": None}
        masked = _mask_config_dict(config)
        # None is a scalar — sensitive key rule applies
        assert masked["secret_access_key"] == "***"

"""Regression tests: AWSProviderConfig credential fields use SecretStr.

Covers:
- str/repr/model_dump never leak the raw secret value
- get_secret_value() returns the real value (for passing to boto3)
- config dict masking in aws_client before logging
- explicit credentials are threaded to boto3.Session (not silently dropped)
- recursive masking of nested dicts and lists
- precise masking rules: public_key/key_file NOT masked; secrets ARE masked
- json.dumps safety on model_dump() output
"""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestAWSProviderConfigSecretStr:
    """SecretStr enforcement on access_key_id and secret_access_key."""

    def _make_config(self, **kwargs):
        from orb.providers.aws.configuration.config import AWSProviderConfig

        return AWSProviderConfig(  # type: ignore[call-arg]
            region="us-east-1",
            **kwargs,
        )

    def test_access_key_id_not_exposed_in_str(self):
        """str(config) must not contain the raw access key value."""
        config = self._make_config(access_key_id="AKIATEST123SECRET")
        assert "AKIATEST123SECRET" not in str(config)

    def test_secret_access_key_not_exposed_in_str(self):
        """str(config) must not contain the raw secret access key."""
        config = self._make_config(secret_access_key="SuP3rS3cr3tV4lue")  # nosec B105
        assert "SuP3rS3cr3tV4lue" not in str(config)

    def test_access_key_id_not_exposed_in_repr(self):
        """repr(config) must not contain the raw access key value."""
        config = self._make_config(access_key_id="AKIATEST123SECRET")
        assert "AKIATEST123SECRET" not in repr(config)

    def test_secret_access_key_not_exposed_in_repr(self):
        """repr(config) must not contain the raw secret access key."""
        config = self._make_config(secret_access_key="SuP3rS3cr3tV4lue")  # nosec B105
        assert "SuP3rS3cr3tV4lue" not in repr(config)

    def test_model_dump_does_not_leak_secret_access_key(self):
        """model_dump() default mode must not expose the raw secret value."""
        config = self._make_config(secret_access_key="SuP3rS3cr3tV4lue")  # nosec B105
        dumped = config.model_dump()
        secret_val = dumped.get("secret_access_key")
        # SecretStr objects in model_dump() are returned as SecretStr, not plain str
        assert secret_val is None or str(secret_val) != "SuP3rS3cr3tV4lue"

    def test_model_dump_does_not_leak_access_key_id(self):
        """model_dump() default mode must not expose the raw access key ID."""
        config = self._make_config(access_key_id="AKIATEST123SECRET")
        dumped = config.model_dump()
        key_val = dumped.get("access_key_id")
        assert key_val is None or str(key_val) != "AKIATEST123SECRET"

    def test_get_secret_value_returns_real_access_key_id(self):
        """get_secret_value() must return the real value so boto3 can use it."""
        config = self._make_config(access_key_id="AKIATEST123SECRET")
        assert config.access_key_id is not None
        assert config.access_key_id.get_secret_value() == "AKIATEST123SECRET"

    def test_get_secret_value_returns_real_secret_access_key(self):
        """get_secret_value() must return the real value so boto3 can use it."""
        config = self._make_config(secret_access_key="SuP3rS3cr3tV4lue")  # nosec B105
        assert config.secret_access_key is not None
        assert config.secret_access_key.get_secret_value() == "SuP3rS3cr3tV4lue"

    def test_none_credentials_remain_none(self):
        """Omitting credentials leaves both fields as None."""
        config = self._make_config()
        assert config.access_key_id is None
        assert config.secret_access_key is None

    # --- json.dumps safety ---

    def test_model_dump_json_mode_is_serialisable(self):
        """model_dump(mode='json') produces a dict that json.dumps can serialise."""
        config = self._make_config(
            access_key_id="AKIATEST123SECRET",
            secret_access_key="SuP3rS3cr3tV4lue",  # nosec B105
        )
        dumped = config.model_dump(mode="json")
        # Must not raise
        serialised = json.dumps(dumped)
        # The raw values are exposed in json mode — that is expected and
        # acceptable when intentionally serialising for internal transfer.
        # The caller is responsible for securing this output.
        assert isinstance(serialised, str)

    def test_model_dump_default_mode_raises_on_json_dumps(self):
        """model_dump() (default mode) contains SecretStr which json.dumps cannot handle."""
        config = self._make_config(
            access_key_id="AKIATEST123SECRET",
            secret_access_key="SuP3rS3cr3tV4lue",  # nosec B105
        )
        dumped = config.model_dump()
        # SecretStr is not JSON-serialisable; json.dumps must raise TypeError.
        # This test documents the known pitfall so callers know to use mode="json"
        # or get_secret_value() before handing the dict to a JSON serialiser.
        with pytest.raises(TypeError):
            json.dumps(dumped)


@pytest.mark.unit
class TestMaskConfigDict:
    """_mask_config_dict hides sensitive keys before logging."""

    def _mask(self, d):
        from orb.providers.aws.infrastructure.aws_client import _mask_config_dict

        return _mask_config_dict(d)

    def test_secret_access_key_masked(self):
        result = self._mask({"secret_access_key": "real-secret", "region": "us-east-1"})
        assert result["secret_access_key"] == "***"
        assert result["region"] == "us-east-1"

    def test_access_key_id_masked(self):
        result = self._mask({"access_key_id": "AKIATEST", "profile": "default"})
        assert result["access_key_id"] == "***"
        assert result["profile"] == "default"

    def test_password_key_masked(self):
        result = self._mask({"db_password": "hunter2"})
        assert result["db_password"] == "***"

    def test_token_key_masked(self):
        result = self._mask({"session_token": "tok123"})
        assert result["session_token"] == "***"

    def test_credential_key_masked(self):
        result = self._mask({"credential_file": "/path/to/creds"})
        assert result["credential_file"] == "***"

    def test_non_sensitive_keys_pass_through(self):
        result = self._mask({"region": "eu-west-1", "endpoint_url": "https://example.com"})
        assert result["region"] == "eu-west-1"
        assert result["endpoint_url"] == "https://example.com"

    def test_empty_dict(self):
        assert self._mask({}) == {}

    def test_masked_value_not_present_in_str_representation(self):
        """The raw secret must not appear anywhere in the masked dict's repr."""
        result = self._mask({"secret_access_key": "SuP3rS3cr3t"})  # nosec B105
        assert "SuP3rS3cr3t" not in str(result)

    # --- Regression: bare "key" must NOT mask legitimate non-secret fields ---

    def test_public_key_not_masked(self):
        """public_key is a legitimate non-secret field and must not be masked."""
        result = self._mask({"public_key": "ssh-rsa AAAA...", "region": "us-east-1"})
        assert result["public_key"] == "ssh-rsa AAAA..."

    def test_key_file_not_masked(self):
        """key_file holds a path, not a secret value, and must not be masked."""
        result = self._mask({"key_file": "/path/to/key.pem"})
        assert result["key_file"] == "/path/to/key.pem"

    def test_region_key_not_masked(self):
        """Hypothetical region_key metadata field must not be masked."""
        result = self._mask({"region_key": "ap-southeast-1"})
        assert result["region_key"] == "ap-southeast-1"

    # --- Regression: recursive masking ---

    def test_nested_dict_secret_masked(self):
        """Secrets nested inside a child dict must be masked recursively."""
        result = self._mask({"auth": {"secret_access_key": "nested-secret", "region": "eu-west-1"}})
        assert result["auth"]["secret_access_key"] == "***"
        assert result["auth"]["region"] == "eu-west-1"

    def test_deeply_nested_secret_masked(self):
        """Secrets two levels deep must still be masked."""
        result = self._mask({"outer": {"inner": {"access_key_id": "deep-key"}}})
        assert result["outer"]["inner"]["access_key_id"] == "***"

    def test_list_of_dicts_secrets_masked(self):
        """Dicts inside a list value must have their secrets masked."""
        result = self._mask(
            {"credentials": [{"secret_access_key": "list-secret"}, {"region": "eu-west-1"}]}
        )
        assert result["credentials"][0]["secret_access_key"] == "***"
        assert result["credentials"][1]["region"] == "eu-west-1"

    def test_list_of_scalars_not_affected(self):
        """Scalar lists are passed through unchanged."""
        result = self._mask({"subnet_ids": ["subnet-aaa", "subnet-bbb"]})
        assert result["subnet_ids"] == ["subnet-aaa", "subnet-bbb"]


@pytest.mark.unit
class TestAWSSessionFactoryCredentials:
    """Explicit credentials are threaded to boto3.Session (C3 regression)."""

    def test_explicit_keys_passed_to_boto3_session(self):
        """boto3.Session must receive aws_access_key_id when the config supplies keys."""
        from orb.providers.aws.session_factory import AWSSessionFactory

        with patch("orb.providers.aws.session_factory.boto3.Session") as mock_session:
            AWSSessionFactory.create_session(
                aws_access_key_id="AKIATEST123",
                aws_secret_access_key="s3cr3t",  # nosec B105
            )
            mock_session.assert_called_once()
            _, kwargs = mock_session.call_args
            assert kwargs.get("aws_access_key_id") == "AKIATEST123"
            assert kwargs.get("aws_secret_access_key") == "s3cr3t"

    def test_absent_keys_do_not_appear_in_boto3_call(self):
        """When no explicit keys are provided boto3.Session must not receive key kwargs."""
        from orb.providers.aws.session_factory import AWSSessionFactory

        with patch("orb.providers.aws.session_factory.boto3.Session") as mock_session:
            AWSSessionFactory.create_session(region="us-east-1")
            _, kwargs = mock_session.call_args
            assert "aws_access_key_id" not in kwargs
            assert "aws_secret_access_key" not in kwargs
            assert "aws_session_token" not in kwargs

    def test_session_token_passed_when_present(self):
        """aws_session_token must be forwarded when supplied."""
        from orb.providers.aws.session_factory import AWSSessionFactory

        with patch("orb.providers.aws.session_factory.boto3.Session") as mock_session:
            AWSSessionFactory.create_session(
                aws_access_key_id="AKIATEST",
                aws_secret_access_key="secret",  # nosec B105
                aws_session_token="token-abc",
            )
            _, kwargs = mock_session.call_args
            assert kwargs.get("aws_session_token") == "token-abc"

    def test_empty_string_keys_not_passed_to_boto3(self):
        """Empty strings must be treated as absent — not forwarded to boto3."""
        from orb.providers.aws.session_factory import AWSSessionFactory

        with patch("orb.providers.aws.session_factory.boto3.Session") as mock_session:
            AWSSessionFactory.create_session(
                aws_access_key_id="",
                aws_secret_access_key="",  # nosec B105
            )
            _, kwargs = mock_session.call_args
            assert "aws_access_key_id" not in kwargs
            assert "aws_secret_access_key" not in kwargs

    def test_aws_client_extracts_secret_str_for_session(self):
        """AWSClient.__init__ must unwrap SecretStr and pass keys to create_session.

        This test patches get_selected_aws_provider_config to bypass the
        provider-lookup machinery and focus purely on the credential-extraction
        and session-creation logic.
        """
        from orb.providers.aws.configuration.config import AWSProviderConfig

        provider_config = AWSProviderConfig(  # type: ignore[call-arg]
            region="us-east-1",
            access_key_id="AKIATEST123",
            secret_access_key="MySuperSecret",  # nosec B105
        )

        config_mock = MagicMock()
        config_mock.get_provider_config.return_value = None
        config_mock.get_metrics_config.return_value = {}

        logger_mock = MagicMock()

        with (
            patch("orb.providers.aws.session_factory.boto3.Session") as mock_boto_session,
            patch(
                "orb.providers.aws.infrastructure.aws_client.AWSClient.get_selected_aws_provider_config",
                return_value=provider_config,
            ),
        ):
            mock_boto_session.return_value = MagicMock()

            from orb.providers.aws.infrastructure.aws_client import AWSClient

            AWSClient(
                config=config_mock,
                logger=logger_mock,
                provider_name=None,
                active_provider_name_resolver=None,
            )

        # boto3.Session must have been called with the unwrapped secret values
        found_call_with_keys = False
        for c in mock_boto_session.call_args_list:
            kw = c[1] if len(c) > 1 else {}
            if kw.get("aws_access_key_id") == "AKIATEST123":
                found_call_with_keys = True
                assert kw.get("aws_secret_access_key") == "MySuperSecret"
                break
        assert found_call_with_keys, (
            "boto3.Session was never called with aws_access_key_id='AKIATEST123'. "
            f"Actual calls: {mock_boto_session.call_args_list}"
        )

"""Focused tests for CycleCloud session settings resolution."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CredentialUnavailableError

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.infrastructure.cyclecloud_session import (
    CycleCloudCredentialData,
    CycleCloudRequestContext,
)
from orb.providers.azure.infrastructure.cyclecloud_session_builder import (
    CycleCloudSessionBuilder,
)


def _make_builder(*, provider_cfg=None, template=None, request_context=None, credential=None):
    token_provider = None
    if credential is not None:
        token_provider = MagicMock()
        token_provider.get_access_token.side_effect = lambda scope: credential.get_token(scope).token
        token_provider.get_auth_error_types.return_value = (
            CredentialUnavailableError,
            ClientAuthenticationError,
        )
    return CycleCloudSessionBuilder(
        cc_url="https://cc.example.com",
        verify_ssl=None,
        template=template,
        request_context=request_context or CycleCloudRequestContext(),
        provider_cfg=provider_cfg,
        token_provider=token_provider,
    )


def test_cyclecloud_request_context_round_trips_metadata():
    context = CycleCloudRequestContext.from_mapping({
        "cluster_name": "my-cluster",
        "node_array": "execute",
        "node_ids": ["node-1", "node-2"],
        "operation_id": "op-123",
        "operation_location": "https://cc.example.com/operations/op-123",
        "added_count": "2",
        "cyclecloud_url": "https://cc.example.com",
        "cyclecloud_credential_path": "config/cc.json",
        "cyclecloud_verify_ssl": False,
        "cyclecloud_auth_mode": "bearer",
        "cyclecloud_aad_scope": "https://cc.example.com/.default",
    })

    assert context.added_count == 2
    assert context.node_ids == ("node-1", "node-2")
    assert context.to_metadata() == {
        "cluster_name": "my-cluster",
        "node_array": "execute",
        "node_ids": ["node-1", "node-2"],
        "operation_id": "op-123",
        "operation_location": "https://cc.example.com/operations/op-123",
        "added_count": 2,
        "cyclecloud_url": "https://cc.example.com",
        "cyclecloud_credential_path": "config/cc.json",
        "cyclecloud_verify_ssl": False,
        "cyclecloud_auth_mode": "bearer",
        "cyclecloud_aad_scope": "https://cc.example.com/.default",
    }


def test_build_settings_returns_no_bearer_token_when_no_token_provider_is_available():
    builder = CycleCloudSessionBuilder(
        cc_url="https://cc.example.com",
        verify_ssl=False,
        template=None,
        request_context=CycleCloudRequestContext.from_mapping(
            {"cyclecloud_auth_mode": "bearer"}
        ),
        provider_cfg=None,
    )

    settings = builder.build_settings()

    assert settings.auth_mode == "bearer"


def test_build_settings_skips_expected_auth_failures_before_returning_bearer_token():
    credential = MagicMock()
    credential.get_token.side_effect = [
        CredentialUnavailableError("missing"),
        ClientAuthenticationError(message="bad token"),
        MagicMock(token="tok-123"),
    ]
    builder = CycleCloudSessionBuilder(
        cc_url="https://cc.example.com",
        verify_ssl=False,
        template=None,
        request_context=CycleCloudRequestContext.from_mapping(
            {
                "cyclecloud_auth_mode": "bearer",
                "cyclecloud_aad_scope": "https://scope-1/.default",
            }
        ),
        provider_cfg=None,
        token_provider=MagicMock(
            get_access_token=MagicMock(side_effect=lambda scope: credential.get_token(scope).token),
            get_auth_error_types=MagicMock(
                return_value=(CredentialUnavailableError, ClientAuthenticationError)
            ),
        ),
    )

    settings = builder.build_settings()

    session = MagicMock()
    session.headers = {}

    resolved_auth_mode = builder.configure_session_auth(session=session, settings=settings)

    assert resolved_auth_mode == "bearer"
    assert session.headers["Authorization"] == "Bearer tok-123"


def test_build_settings_propagates_unexpected_token_errors():
    credential = MagicMock()
    credential.get_token.side_effect = RuntimeError("boom")
    builder = CycleCloudSessionBuilder(
        cc_url="https://cc.example.com",
        verify_ssl=False,
        template=None,
        request_context=CycleCloudRequestContext.from_mapping(
            {"cyclecloud_auth_mode": "bearer"}
        ),
        provider_cfg=None,
        token_provider=MagicMock(
            get_access_token=MagicMock(side_effect=lambda scope: credential.get_token(scope).token),
            get_auth_error_types=MagicMock(
                return_value=(CredentialUnavailableError, ClientAuthenticationError)
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="boom"):
        settings = builder.build_settings()
        builder.configure_session_auth(session=MagicMock(headers={}), settings=settings)


def test_build_settings_loads_cyclecloud_config_from_provider():
    provider_cfg = AzureProviderConfig(
        region="eastus2",
        resource_group="orb-test-rg",
        cyclecloud={
            "credential_path": "config/cyclecloud-credentials.json",
            "url": "https://cc.example.com",
            "verify_ssl": False,
        },
    )
    builder = _make_builder(provider_cfg=provider_cfg)
    builder._load_credential_file = MagicMock(  # type: ignore[method-assign]
        return_value=CycleCloudCredentialData(
            username="cc_admin",
            password="changeme",
        )
    )

    settings = builder.build_settings()

    assert settings.base_url == "https://cc.example.com"
    assert settings.verify_ssl is False
    assert settings.auth_mode is None
    assert settings.credential_path == "config/cyclecloud-credentials.json"

    session = MagicMock()
    session.headers = {}
    resolved_auth_mode = builder.configure_session_auth(session=session, settings=settings)
    assert resolved_auth_mode == "basic"
    assert session.auth == ("cc_admin", "changeme")


def test_build_settings_loads_credentials_from_file(tmp_path: Path):
    credential_file = tmp_path / "cyclecloud-credentials.json"
    credential_file.write_text(
        json.dumps(
            {
                "username": "file-admin",
                "password": "file-secret",
                "auth_mode": "basic",
            }
        ),
        encoding="utf-8",
    )
    builder = CycleCloudSessionBuilder(
        cc_url="https://cc.example.com",
        verify_ssl=False,
        template=None,
        request_context=CycleCloudRequestContext.from_mapping(
            {"cyclecloud_credential_path": str(credential_file)}
        ),
        provider_cfg=None,
    )

    settings = builder.build_settings()

    assert settings.base_url == "https://cc.example.com"
    assert settings.verify_ssl is False
    assert settings.credential_path == str(credential_file)

    session = MagicMock()
    session.headers = {}
    resolved_auth_mode = builder.configure_session_auth(session=session, settings=settings)
    assert resolved_auth_mode == "basic"
    assert session.auth == ("file-admin", "file-secret")


def test_build_settings_parses_verify_ssl_string_from_request_context():
    builder = CycleCloudSessionBuilder(
        cc_url="https://cc.example.com",
        verify_ssl=None,
        template=None,
        request_context=CycleCloudRequestContext.from_mapping(
            {
                "cyclecloud_verify_ssl": "false",
                "cyclecloud_auth_mode": "bearer",
                "cyclecloud_aad_scope": "https://cc.example.com/.default",
            }
        ),
        provider_cfg=None,
    )
    builder._get_azure_bearer_token = MagicMock(return_value="tok-123")  # type: ignore[method-assign]

    settings = builder.build_settings()

    assert settings.verify_ssl is False
    session = MagicMock()
    session.headers = {}
    resolved_auth_mode = builder.configure_session_auth(session=session, settings=settings)
    assert resolved_auth_mode == "bearer"
    assert session.headers["Authorization"] == "Bearer tok-123"


def test_build_settings_takes_verify_ssl_from_credential_file(tmp_path: Path):
    credential_file = tmp_path / "cyclecloud-credentials.json"
    credential_file.write_text(
        json.dumps(
            {
                "url": "https://cc.example.com",
                "username": "file-admin",
                "password": "file-secret",
                "verify_ssl": "false",
            }
        ),
        encoding="utf-8",
    )
    builder = CycleCloudSessionBuilder(
        cc_url=None,
        verify_ssl=None,
        template=None,
        request_context=CycleCloudRequestContext.from_mapping(
            {"cyclecloud_credential_path": str(credential_file)}
        ),
        provider_cfg=None,
    )

    settings = builder.build_settings()

    assert settings.base_url == "https://cc.example.com"
    assert settings.verify_ssl is False
    assert settings.credential_path == str(credential_file)


def test_resolve_cascaded_value_skips_blank_values_and_uses_default():
    builder = CycleCloudSessionBuilder(
        cc_url=None,
        verify_ssl=None,
        template=None,
        request_context=CycleCloudRequestContext(),
        provider_cfg=None,
    )

    resolved = builder._resolve_cascaded_value(None, "", False, default=True)

    assert resolved is False


def test_build_settings_resolves_url_from_request_context_before_provider_config():
    provider_cfg = AzureProviderConfig(
        region="eastus2",
        resource_group="orb-test-rg",
        cyclecloud={
            "url": "https://provider.example.com",
            "verify_ssl": True,
        },
    )
    builder = CycleCloudSessionBuilder(
        cc_url=None,
        verify_ssl=None,
        template=None,
        request_context=CycleCloudRequestContext.from_mapping(
            {"cyclecloud_url": "https://request.example.com"}
        ),
        provider_cfg=provider_cfg,
    )

    settings = builder.build_settings()

    assert settings.base_url == "https://request.example.com"

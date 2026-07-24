"""Focused tests for CycleCloud session settings resolution."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CredentialUnavailableError

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.exceptions.azure_exceptions import CycleCloudConnectionError
from orb.providers.azure.infrastructure.cyclecloud_session import (
    CycleCloudCredentialData,
    CycleCloudRequestContext,
)
from orb.providers.azure.infrastructure.cyclecloud_session_builder import (
    CycleCloudSessionBuilder,
)


def _provider_config(
    *,
    url: str | None = "https://cc.example.com",
    credential_path: str | None = None,
    verify_ssl: bool | None = False,
    auth_mode: str | None = None,
    aad_scope: str | None = None,
) -> AzureProviderConfig:
    return AzureProviderConfig(
        region="eastus2",
        resource_group="orb-test-rg",
        cyclecloud={
            "url": url,
            "credential_path": credential_path,
            "verify_ssl": verify_ssl,
            "auth_mode": auth_mode,
            "aad_scope": aad_scope,
        },
    )


def _make_builder(*, provider_cfg=None, credential=None):
    async_token_provider = None
    if credential is not None:
        async_token_provider = MagicMock()
        async_token_provider.get_access_token = AsyncMock(
            side_effect=lambda scope: credential.get_token(scope).token
        )
        async_token_provider.get_auth_error_types.return_value = (
            CredentialUnavailableError,
            ClientAuthenticationError,
        )
    return CycleCloudSessionBuilder(
        provider_cfg=provider_cfg or _provider_config(),
        async_token_provider=async_token_provider,
    )


def test_cyclecloud_request_context_round_trips_metadata():
    context = CycleCloudRequestContext.from_mapping(
        {
            "cluster_name": "my-cluster",
            "node_array": "execute",
            "node_ids": ["node-1", "node-2"],
            "operation_id": "op-123",
            "operation_location": "https://cc.example.com/operations/op-123",
            "added_count": "2",
        }
    )

    assert context.added_count == 2
    assert context.node_ids == ("node-1", "node-2")
    assert context.to_metadata() == {
        "cluster_name": "my-cluster",
        "node_array": "execute",
        "node_ids": ["node-1", "node-2"],
        "operation_id": "op-123",
        "operation_location": "https://cc.example.com/operations/op-123",
        "added_count": 2,
    }


def test_build_settings_returns_bearer_mode_when_no_token_provider_is_available():
    builder = _make_builder(provider_cfg=_provider_config(auth_mode="bearer"))

    settings = builder.build_settings()

    assert settings.auth_mode == "bearer"


@pytest.mark.asyncio
async def test_resolve_async_auth_skips_expected_auth_failures_before_returning_bearer_token():
    credential = MagicMock()
    credential.get_token.side_effect = [
        CredentialUnavailableError("missing"),
        ClientAuthenticationError(message="bad token"),
        MagicMock(token="tok-123"),
    ]
    builder = CycleCloudSessionBuilder(
        provider_cfg=_provider_config(
            auth_mode="bearer",
            aad_scope="https://scope-1/.default",
        ),
        async_token_provider=MagicMock(
            get_access_token=AsyncMock(side_effect=lambda scope: credential.get_token(scope).token),
            get_auth_error_types=MagicMock(
                return_value=(CredentialUnavailableError, ClientAuthenticationError)
            ),
        ),
    )

    settings = builder.build_settings()
    headers, auth, resolved_auth_mode = await builder.resolve_async_auth(settings=settings)

    assert resolved_auth_mode == "bearer"
    assert auth is None
    assert headers["Authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_resolve_async_auth_propagates_unexpected_token_errors():
    credential = MagicMock()
    credential.get_token.side_effect = RuntimeError("boom")
    builder = CycleCloudSessionBuilder(
        provider_cfg=_provider_config(auth_mode="bearer"),
        async_token_provider=MagicMock(
            get_access_token=AsyncMock(side_effect=lambda scope: credential.get_token(scope).token),
            get_auth_error_types=MagicMock(
                return_value=(CredentialUnavailableError, ClientAuthenticationError)
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="boom"):
        settings = builder.build_settings()
        await builder.resolve_async_auth(settings=settings)


@pytest.mark.asyncio
async def test_resolve_async_auth_uses_async_token_provider():
    async_token_provider = MagicMock()
    async_token_provider.get_access_token = AsyncMock(
        side_effect=[
            CredentialUnavailableError("missing"),
            "tok-async-123",
        ]
    )
    async_token_provider.get_auth_error_types.return_value = (
        CredentialUnavailableError,
        ClientAuthenticationError,
    )
    builder = CycleCloudSessionBuilder(
        provider_cfg=_provider_config(
            auth_mode="bearer",
            aad_scope="https://scope-1/.default",
        ),
        async_token_provider=async_token_provider,
    )

    settings = builder.build_settings()

    headers, auth, resolved_auth_mode = await builder.resolve_async_auth(settings=settings)

    assert resolved_auth_mode == "bearer"
    assert auth is None
    assert headers["Authorization"] == "Bearer tok-async-123"


@pytest.mark.asyncio
async def test_resolve_async_auth_rejects_ssh_mode():
    builder = _make_builder(provider_cfg=_provider_config(auth_mode="ssh"))

    with pytest.raises(CycleCloudConnectionError, match="not supported"):
        settings = builder.build_settings()
        await builder.resolve_async_auth(settings=settings)


@pytest.mark.asyncio
async def test_resolve_async_auth_errors_when_bearer_requested_but_unavailable():
    async_token_provider = MagicMock()
    async_token_provider.get_access_token = AsyncMock(
        side_effect=CredentialUnavailableError("missing")
    )
    async_token_provider.get_auth_error_types.return_value = (
        CredentialUnavailableError,
        ClientAuthenticationError,
    )
    builder = CycleCloudSessionBuilder(
        provider_cfg=_provider_config(auth_mode="bearer"),
        async_token_provider=async_token_provider,
    )

    with pytest.raises(CycleCloudConnectionError, match="no bearer token could be resolved"):
        settings = builder.build_settings()
        await builder.resolve_async_auth(settings=settings)


@pytest.mark.asyncio
async def test_resolve_async_auth_errors_when_no_auth_method_resolves():
    builder = _make_builder()

    with pytest.raises(CycleCloudConnectionError, match="No CycleCloud auth method resolved"):
        settings = builder.build_settings()
        await builder.resolve_async_auth(settings=settings)


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
    assert settings.credential_data.username == "cc_admin"


def test_cyclecloud_credential_data_repr_masks_secret_fields():
    credential_data = CycleCloudCredentialData(
        url="https://cc.example.com",
        auth_mode="bearer",
        username="cc_admin",
        password="changeme",
        bearer_token="tok-123",
        aad_scope="https://cc.example.com/.default",
    )

    credential_repr = repr(credential_data)

    assert "cc_admin" not in credential_repr
    assert "changeme" not in credential_repr
    assert "tok-123" not in credential_repr
    assert "https://cc.example.com" in credential_repr
    assert "bearer" in credential_repr


@pytest.mark.asyncio
async def test_resolve_async_auth_loads_credentials_from_file(tmp_path: Path):
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
        provider_cfg=_provider_config(credential_path=str(credential_file)),
    )

    settings = builder.build_settings()

    assert settings.base_url == "https://cc.example.com"
    assert settings.verify_ssl is False
    assert settings.credential_data.username == "file-admin"

    headers, auth, resolved_auth_mode = await builder.resolve_async_auth(settings=settings)
    assert headers == {}
    assert resolved_auth_mode == "basic"
    assert isinstance(auth, httpx.BasicAuth)


def test_build_settings_takes_verify_ssl_from_provider_config():
    builder = _make_builder(provider_cfg=_provider_config(verify_ssl=False))
    settings = builder.build_settings()

    assert settings.verify_ssl is False


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
        provider_cfg=_provider_config(
            url=None,
            credential_path=str(credential_file),
            verify_ssl=None,
        ),
    )

    settings = builder.build_settings()

    assert settings.base_url == "https://cc.example.com"
    assert settings.verify_ssl is False
    assert settings.credential_data.username == "file-admin"


def test_missing_credential_file_error_does_not_expose_configured_path(tmp_path: Path):
    credential_path = tmp_path / "private" / "cyclecloud-credentials.json"
    builder = _make_builder(provider_cfg=_provider_config(credential_path=str(credential_path)))

    with pytest.raises(CycleCloudConnectionError) as exc_info:
        builder.build_settings()

    assert str(credential_path) not in str(exc_info.value)


def test_credential_file_rejects_unsupported_fields(tmp_path: Path):
    credential_file = tmp_path / "cyclecloud-credentials.json"
    credential_file.write_text(
        json.dumps({"username": "admin", "password": "secret", "unexpected": True}),
        encoding="utf-8",
    )
    builder = _make_builder(provider_cfg=_provider_config(credential_path=str(credential_file)))

    with pytest.raises(CycleCloudConnectionError, match="unsupported fields: unexpected"):
        builder.build_settings()


def test_resolve_cascaded_value_skips_blank_values_and_uses_default():
    builder = _make_builder()

    resolved = builder._resolve_cascaded_value(None, "", False, default=True)

    assert resolved is False


def test_build_settings_uses_provider_url():
    provider_cfg = AzureProviderConfig(
        region="eastus2",
        resource_group="orb-test-rg",
        cyclecloud={
            "url": "https://provider.example.com",
            "verify_ssl": True,
        },
    )
    builder = CycleCloudSessionBuilder(provider_cfg=provider_cfg)

    settings = builder.build_settings()

    assert settings.base_url == "https://provider.example.com"

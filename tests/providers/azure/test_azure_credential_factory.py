"""Focused tests for Azure credential factory helpers."""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.providers.azure.infrastructure.credential_factory import (
    AsyncAzureCredentialAccessTokenProvider,
    AsyncDefaultAzureAccessTokenProvider,
    AzureCredentialAccessTokenProvider,
    DefaultAzureAccessTokenProvider,
    create_default_azure_credential,
    create_default_azure_credential_async,
    get_default_azure_credential_error_types,
)


def test_azure_credential_access_token_provider_adapts_existing_credential():
    credential = MagicMock()
    credential.get_token.return_value = types.SimpleNamespace(token="access-token")

    provider = AzureCredentialAccessTokenProvider(credential)

    assert provider.get_access_token("scope") == "access-token"
    credential.get_token.assert_called_once_with("scope")


def test_create_default_azure_credential_passes_managed_identity_client_id_when_configured():
    fake_identity = types.ModuleType("azure.identity")
    fake_ctor = MagicMock(return_value=MagicMock())
    fake_identity.DefaultAzureCredential = fake_ctor

    with patch.dict(
        sys.modules,
        {
            "azure": types.ModuleType("azure"),
            "azure.identity": fake_identity,
        },
    ):
        credential = create_default_azure_credential(
            client_id="managed-identity-client-id",
            logger=MagicMock(),
        )

    assert credential is fake_ctor.return_value
    fake_ctor.assert_called_once_with(managed_identity_client_id="managed-identity-client-id")


def test_create_default_azure_credential_omits_managed_identity_client_id_when_unset():
    fake_identity = types.ModuleType("azure.identity")
    fake_ctor = MagicMock(return_value=MagicMock())
    fake_identity.DefaultAzureCredential = fake_ctor

    with patch.dict(
        sys.modules,
        {
            "azure": types.ModuleType("azure"),
            "azure.identity": fake_identity,
        },
    ):
        create_default_azure_credential(
            client_id=None,
            logger=MagicMock(),
        )

    fake_ctor.assert_called_once_with()


def test_create_default_azure_credential_async_preserves_nested_import_error():
    fake_identity_aio = types.ModuleType("azure.identity.aio")
    fake_ctor = MagicMock(side_effect=ImportError("aiohttp package is not installed"))
    fake_identity_aio.DefaultAzureCredential = fake_ctor
    logger = MagicMock()

    with patch.dict(
        sys.modules,
        {
            "azure": types.ModuleType("azure"),
            "azure.identity": types.ModuleType("azure.identity"),
            "azure.identity.aio": fake_identity_aio,
        },
    ):
        with pytest.raises(ImportError, match="aiohttp package is not installed"):
            create_default_azure_credential_async(
                client_id=None,
                logger=logger,
            )

    logger.error.assert_called_once_with(
        "azure-identity dependency error: aiohttp package is not installed"
    )


@pytest.mark.asyncio
async def test_async_azure_credential_access_token_provider_adapts_existing_credential():
    credential = MagicMock()
    credential.get_token = AsyncMock(return_value=types.SimpleNamespace(token="access-token"))

    provider = AsyncAzureCredentialAccessTokenProvider(credential)

    assert await provider.get_access_token("scope") == "access-token"
    credential.get_token.assert_awaited_once_with("scope")


def test_create_default_azure_credential_async_passes_managed_identity_client_id_when_configured():
    fake_identity_aio = types.ModuleType("azure.identity.aio")
    fake_ctor = MagicMock(return_value=MagicMock())
    fake_identity_aio.DefaultAzureCredential = fake_ctor

    with patch.dict(
        sys.modules,
        {
            "azure": types.ModuleType("azure"),
            "azure.identity": types.ModuleType("azure.identity"),
            "azure.identity.aio": fake_identity_aio,
        },
    ):
        credential = create_default_azure_credential_async(
            client_id="managed-identity-client-id",
            logger=MagicMock(),
        )

    assert credential is fake_ctor.return_value
    fake_ctor.assert_called_once_with(managed_identity_client_id="managed-identity-client-id")


def test_default_azure_access_token_provider_closes_short_lived_credential():
    fake_identity = types.ModuleType("azure.identity")
    fake_credential = MagicMock()
    fake_credential.get_token.return_value = types.SimpleNamespace(token="access-token")
    fake_ctor = MagicMock(return_value=fake_credential)
    fake_identity.DefaultAzureCredential = fake_ctor

    with patch.dict(
        sys.modules,
        {
            "azure": types.ModuleType("azure"),
            "azure.identity": fake_identity,
        },
    ):
        provider = DefaultAzureAccessTokenProvider(
            logger=MagicMock(),
            client_id=None,
        )
        token = provider.get_access_token("https://management.azure.com/.default")

    assert token == "access-token"
    fake_credential.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_async_default_azure_access_token_provider_closes_short_lived_credential():
    fake_identity_aio = types.ModuleType("azure.identity.aio")
    fake_credential = MagicMock()
    fake_credential.get_token = AsyncMock(return_value=types.SimpleNamespace(token="access-token"))
    fake_credential.close = AsyncMock()
    fake_ctor = MagicMock(return_value=fake_credential)
    fake_identity_aio.DefaultAzureCredential = fake_ctor

    with patch.dict(
        sys.modules,
        {
            "azure": types.ModuleType("azure"),
            "azure.identity": types.ModuleType("azure.identity"),
            "azure.identity.aio": fake_identity_aio,
        },
    ):
        provider = AsyncDefaultAzureAccessTokenProvider(
            logger=MagicMock(),
            client_id=None,
        )
        token = await provider.get_access_token("https://management.azure.com/.default")

    assert token == "access-token"
    fake_credential.close.assert_awaited_once_with()


def test_get_default_azure_credential_error_types_returns_sdk_types_when_available():
    fake_core = types.ModuleType("azure.core.exceptions")
    fake_identity = types.ModuleType("azure.identity")

    class FakeClientAuthenticationError(Exception):
        pass

    class FakeCredentialUnavailableError(Exception):
        pass

    fake_core.ClientAuthenticationError = FakeClientAuthenticationError
    fake_identity.CredentialUnavailableError = FakeCredentialUnavailableError

    with patch.dict(
        sys.modules,
        {
            "azure": types.ModuleType("azure"),
            "azure.core": types.ModuleType("azure.core"),
            "azure.core.exceptions": fake_core,
            "azure.identity": fake_identity,
        },
    ):
        assert get_default_azure_credential_error_types() == (
            FakeCredentialUnavailableError,
            FakeClientAuthenticationError,
        )


def test_default_azure_access_token_provider_reports_import_error_as_expected_auth_type():
    provider = DefaultAzureAccessTokenProvider(
        logger=MagicMock(),
        client_id=None,
    )

    assert ImportError in provider.get_auth_error_types()

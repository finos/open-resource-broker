"""Focused tests for Azure SSH key resolution."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from orb.providers.azure.infrastructure.services.ssh_key_resolver import resolve_ssh_keys_async


@pytest.mark.asyncio
async def test_resolve_ssh_keys_async_returns_inline_keys_without_sdk_lookup():
    compute_client = MagicMock()

    result = await resolve_ssh_keys_async(
        ssh_key_name="orb-key",
        ssh_public_keys=["ssh-rsa inline test@host"],
        resource_group="test-rg",
        compute_client=compute_client,
    )

    assert result == ["ssh-rsa inline test@host"]
    compute_client.ssh_public_keys.get.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_ssh_keys_async_fetches_named_azure_ssh_key():
    compute_client = MagicMock()
    ssh_resource = MagicMock()
    ssh_resource.public_key = "ssh-rsa resolved test@host"
    compute_client.ssh_public_keys.get = AsyncMock(return_value=ssh_resource)

    result = await resolve_ssh_keys_async(
        ssh_key_name="orb-key",
        ssh_public_keys=[],
        resource_group="test-rg",
        compute_client=compute_client,
    )

    assert result == ["ssh-rsa resolved test@host"]


@pytest.mark.asyncio
async def test_resolve_ssh_keys_async_requires_name_or_inline_key_data():
    with pytest.raises(ValueError, match="neither 'ssh_key_name' nor 'ssh_public_keys'"):
        await resolve_ssh_keys_async(
            ssh_key_name=None,
            ssh_public_keys=[],
            resource_group="test-rg",
            compute_client=MagicMock(),
        )


@pytest.mark.asyncio
async def test_resolve_ssh_keys_async_rejects_missing_ssh_key_resource():
    compute_client = MagicMock()
    compute_client.ssh_public_keys.get = AsyncMock(
        side_effect=ResourceNotFoundError("missing")
    )

    with pytest.raises(ValueError, match="was not found"):
        await resolve_ssh_keys_async(
            ssh_key_name="orb-key",
            ssh_public_keys=[],
            resource_group="test-rg",
            compute_client=compute_client,
        )


@pytest.mark.asyncio
async def test_resolve_ssh_keys_async_rejects_transport_error():
    compute_client = MagicMock()
    compute_client.ssh_public_keys.get = AsyncMock(side_effect=HttpResponseError("boom"))

    with pytest.raises(ValueError, match="Failed to resolve Azure SSH Public Key"):
        await resolve_ssh_keys_async(
            ssh_key_name="orb-key",
            ssh_public_keys=[],
            resource_group="test-rg",
            compute_client=compute_client,
        )

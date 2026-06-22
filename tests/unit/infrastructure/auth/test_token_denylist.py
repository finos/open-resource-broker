"""Tests for token denylist implementations."""

import time

import pytest

from orb.infrastructure.auth.token_denylist import InMemoryTokenDenylist


@pytest.mark.asyncio
async def test_in_memory_denylist_add_token():
    """Test adding token to denylist."""
    denylist = InMemoryTokenDenylist()

    token = "test_token_123"
    expires_at = int(time.time()) + 3600

    result = await denylist.add_token(token, expires_at)
    assert result is True

    is_denylisted = await denylist.is_denylisted(token)
    assert is_denylisted is True


@pytest.mark.asyncio
async def test_in_memory_denylist_remove_token():
    """Test removing token from denylist."""
    denylist = InMemoryTokenDenylist()

    token = "test_token_123"
    await denylist.add_token(token)

    result = await denylist.remove_token(token)
    assert result is True

    is_denylisted = await denylist.is_denylisted(token)
    assert is_denylisted is False


@pytest.mark.asyncio
async def test_in_memory_denylist_expired_token():
    """Test that expired tokens are automatically removed."""
    denylist = InMemoryTokenDenylist()

    token = "test_token_123"
    expires_at = int(time.time()) - 1  # Already expired

    await denylist.add_token(token, expires_at)

    # Token should be removed when checked
    is_denylisted = await denylist.is_denylisted(token)
    assert is_denylisted is False


@pytest.mark.asyncio
async def test_in_memory_denylist_cleanup():
    """Test cleanup of expired tokens."""
    denylist = InMemoryTokenDenylist()

    # Add expired token
    expired_token = "expired_token"
    await denylist.add_token(expired_token, int(time.time()) - 1)

    # Add valid token
    valid_token = "valid_token"
    await denylist.add_token(valid_token, int(time.time()) + 3600)

    # Run cleanup
    removed = await denylist.cleanup_expired()
    assert removed == 1

    # Valid token should still be there
    assert await denylist.is_denylisted(valid_token) is True


@pytest.mark.asyncio
async def test_in_memory_denylist_size():
    """Test getting denylist size."""
    denylist = InMemoryTokenDenylist()

    assert await denylist.get_denylist_size() == 0

    await denylist.add_token("token1")
    await denylist.add_token("token2")

    assert await denylist.get_denylist_size() == 2

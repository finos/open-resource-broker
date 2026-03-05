"""Tests for token blacklist implementations."""

import time

import pytest

from infrastructure.auth.token_blacklist import InMemoryTokenBlacklist


@pytest.mark.asyncio
async def test_in_memory_blacklist_add_token():
    """Test adding token to blacklist."""
    blacklist = InMemoryTokenBlacklist()

    token = "test_token_123"
    expires_at = int(time.time()) + 3600

    result = await blacklist.add_token(token, expires_at)
    assert result is True

    is_blacklisted = await blacklist.is_blacklisted(token)
    assert is_blacklisted is True


@pytest.mark.asyncio
async def test_in_memory_blacklist_remove_token():
    """Test removing token from blacklist."""
    blacklist = InMemoryTokenBlacklist()

    token = "test_token_123"
    await blacklist.add_token(token)

    result = await blacklist.remove_token(token)
    assert result is True

    is_blacklisted = await blacklist.is_blacklisted(token)
    assert is_blacklisted is False


@pytest.mark.asyncio
async def test_in_memory_blacklist_expired_token():
    """Test that expired tokens are automatically removed."""
    blacklist = InMemoryTokenBlacklist()

    token = "test_token_123"
    expires_at = int(time.time()) - 1  # Already expired

    await blacklist.add_token(token, expires_at)

    # Token should be removed when checked
    is_blacklisted = await blacklist.is_blacklisted(token)
    assert is_blacklisted is False


@pytest.mark.asyncio
async def test_in_memory_blacklist_cleanup():
    """Test cleanup of expired tokens."""
    blacklist = InMemoryTokenBlacklist()

    # Add expired token
    expired_token = "expired_token"
    await blacklist.add_token(expired_token, int(time.time()) - 1)

    # Add valid token
    valid_token = "valid_token"
    await blacklist.add_token(valid_token, int(time.time()) + 3600)

    # Run cleanup
    removed = await blacklist.cleanup_expired()
    assert removed == 1

    # Valid token should still be there
    assert await blacklist.is_blacklisted(valid_token) is True


@pytest.mark.asyncio
async def test_in_memory_blacklist_size():
    """Test getting blacklist size."""
    blacklist = InMemoryTokenBlacklist()

    assert await blacklist.get_blacklist_size() == 0

    await blacklist.add_token("token1")
    await blacklist.add_token("token2")

    assert await blacklist.get_blacklist_size() == 2

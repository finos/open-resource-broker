"""Unit tests for the loopback-admin token machinery in ``orb.api.server``.

Covers the daemon-issued loopback token capability that lets the CLI reload
command and the live REST tests authenticate as admin over the loopback IPC:

- ``_LoopbackAdminAuthWrapper``: token acceptance (constant-time), non-ASCII
  rejection, mtime-based auto-reload, ``rotate_token`` force reload,
  delegation to the inner strategy for non-matching tokens, and the
  loopback-peer check (direct peer only, forwarded headers ignored).
- ``_LoopbackAdminTokenMiddleware``: stamps ``request.state`` with the admin
  role for a valid token and leaves it untouched otherwise.
- ``_load_loopback_token``: reads the daemon-written token file and registers
  it, skipping silently when the file is absent.

These are pure-logic units exercised directly (no running server), matching the
loopback capability's design of staying fully isolated from the JWT strategy.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.api.server import (
    _load_loopback_token,
    _LoopbackAdminAuthWrapper,
    _LoopbackAdminTokenMiddleware,
)
from orb.infrastructure.adapters.ports.auth import AuthResult, AuthStatus


@pytest.fixture(autouse=True)
def _reset_loopback_tokens():
    """Isolate the class-level token state between tests.

    ``_LoopbackAdminAuthWrapper`` stores its token set / file on the class so it
    survives module reloads; that means it also leaks across tests unless reset.
    """
    prev_tokens = set(_LoopbackAdminAuthWrapper._tokens)
    prev_file = _LoopbackAdminAuthWrapper._token_file
    prev_mtime = _LoopbackAdminAuthWrapper._token_file_mtime
    _LoopbackAdminAuthWrapper._tokens = set()
    _LoopbackAdminAuthWrapper._token_file = None
    _LoopbackAdminAuthWrapper._token_file_mtime = 0.0
    yield
    _LoopbackAdminAuthWrapper._tokens = prev_tokens
    _LoopbackAdminAuthWrapper._token_file = prev_file
    _LoopbackAdminAuthWrapper._token_file_mtime = prev_mtime


def _ctx(
    auth_header: str = "",
    path: str = "/api/v1/machines/request",
    direct_client_ip: str | None = "127.0.0.1",
    forwarded_for: str | None = None,
):
    """Minimal auth context.

    ``direct_client_ip`` models ``AuthContext.direct_client_ip`` — the raw
    socket peer address, never resolved through a forwarded header. Defaults
    to loopback so existing token-matching tests are unaffected by the peer
    check. ``forwarded_for`` is only included in headers to prove it is never
    consulted for the loopback decision.
    """
    headers = {"authorization": auth_header}
    if forwarded_for is not None:
        headers["x-forwarded-for"] = forwarded_for
    return SimpleNamespace(headers=headers, path=path, direct_client_ip=direct_client_ip)


@pytest.mark.unit
@pytest.mark.api
class TestLoopbackAuthWrapperAuthenticate:
    @pytest.mark.asyncio
    async def test_matching_token_grants_admin_identity(self):
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        inner = MagicMock()
        inner.authenticate = AsyncMock()
        wrapper = _LoopbackAdminAuthWrapper(inner)

        result = await wrapper.authenticate(_ctx("Bearer secret-token"))

        assert result.status == AuthStatus.SUCCESS
        assert result.user_id == "loopback-admin"
        assert result.user_roles == ["admin"]
        assert result.permissions == ["*"]
        assert result.metadata["strategy"] == "loopback_admin_token"
        # The loopback token short-circuits — the inner strategy is never asked.
        inner.authenticate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_matching_token_delegates_to_inner(self):
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        inner = MagicMock()
        inner_result = AuthResult(status=AuthStatus.INVALID, user_id=None)
        inner.authenticate = AsyncMock(return_value=inner_result)
        wrapper = _LoopbackAdminAuthWrapper(inner)

        result = await wrapper.authenticate(_ctx("Bearer some-other-token"))

        assert result is inner_result
        inner.authenticate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_bearer_prefix_delegates_to_inner(self):
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        inner = MagicMock()
        inner.authenticate = AsyncMock(return_value=AuthResult(status=AuthStatus.INVALID))
        wrapper = _LoopbackAdminAuthWrapper(inner)

        await wrapper.authenticate(_ctx("Basic Zm9vOmJhcg=="))

        inner.authenticate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_ascii_token_returns_invalid_without_delegating(self):
        """A non-ASCII bearer token can never match the ASCII loopback secret.

        It must return INVALID rather than crashing or silently skipping the
        auth stamp (which could be an auth bypass).
        """
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        inner = MagicMock()
        inner.authenticate = AsyncMock()
        wrapper = _LoopbackAdminAuthWrapper(inner)

        result = await wrapper.authenticate(_ctx("Bearer ünïcödé"))

        assert result.status == AuthStatus.INVALID
        assert result.user_id is None
        inner.authenticate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_bearer_token_delegates_to_inner(self):
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        inner = MagicMock()
        inner.authenticate = AsyncMock(return_value=AuthResult(status=AuthStatus.INVALID))
        wrapper = _LoopbackAdminAuthWrapper(inner)

        await wrapper.authenticate(_ctx("Bearer "))

        inner.authenticate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_matching_token_from_loopback_peer_grants_admin_identity(self):
        """Explicit loopback-peer case for the auth-middleware-facing wrapper."""
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        inner = MagicMock()
        inner.authenticate = AsyncMock()
        wrapper = _LoopbackAdminAuthWrapper(inner)

        result = await wrapper.authenticate(
            _ctx("Bearer secret-token", direct_client_ip="127.0.0.1")
        )

        assert result.status == AuthStatus.SUCCESS
        assert result.user_id == "loopback-admin"
        inner.authenticate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_matching_token_from_non_loopback_peer_falls_through_to_inner(self):
        """A correct token from a non-loopback peer must never grant admin."""
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        inner = MagicMock()
        inner_result = AuthResult(status=AuthStatus.INVALID, user_id=None)
        inner.authenticate = AsyncMock(return_value=inner_result)
        wrapper = _LoopbackAdminAuthWrapper(inner)

        result = await wrapper.authenticate(
            _ctx("Bearer secret-token", direct_client_ip="203.0.113.5")
        )

        assert result is inner_result
        inner.authenticate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_matching_token_with_missing_peer_falls_through_to_inner(self):
        """A missing direct peer (e.g. unusual transport) must never be treated as loopback."""
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        inner = MagicMock()
        inner_result = AuthResult(status=AuthStatus.INVALID, user_id=None)
        inner.authenticate = AsyncMock(return_value=inner_result)
        wrapper = _LoopbackAdminAuthWrapper(inner)

        result = await wrapper.authenticate(_ctx("Bearer secret-token", direct_client_ip=None))

        assert result is inner_result
        inner.authenticate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_matching_token_ignores_forwarded_for_from_non_loopback_peer(self):
        """X-Forwarded-For claiming loopback must not override the real peer."""
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        inner = MagicMock()
        inner_result = AuthResult(status=AuthStatus.INVALID, user_id=None)
        inner.authenticate = AsyncMock(return_value=inner_result)
        wrapper = _LoopbackAdminAuthWrapper(inner)

        result = await wrapper.authenticate(
            _ctx(
                "Bearer secret-token",
                direct_client_ip="203.0.113.5",
                forwarded_for="127.0.0.1",
            )
        )

        assert result is inner_result
        inner.authenticate.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.api
class TestLoopbackAuthWrapperDelegation:
    def test_get_strategy_name_delegates(self):
        inner = MagicMock()
        inner.get_strategy_name.return_value = "jwt"
        assert _LoopbackAdminAuthWrapper(inner).get_strategy_name() == "jwt"

    def test_is_enabled_delegates(self):
        inner = MagicMock()
        inner.is_enabled.return_value = True
        assert _LoopbackAdminAuthWrapper(inner).is_enabled() is True

    def test_getattr_delegates_unknown_attribute(self):
        inner = MagicMock()
        inner.some_custom_method.return_value = "delegated"
        wrapper = _LoopbackAdminAuthWrapper(inner)
        assert wrapper.some_custom_method() == "delegated"


@pytest.mark.unit
@pytest.mark.api
class TestLoopbackTokenReload:
    def test_rotate_token_noop_when_no_file_registered(self):
        _LoopbackAdminAuthWrapper._token_file = None
        # Must not raise when there is nothing to reload.
        _LoopbackAdminAuthWrapper.rotate_token()

    def test_rotate_token_reloads_from_file(self, tmp_path):
        token_file = tmp_path / "orb-server.token"
        token_file.write_text("fresh-token", encoding="ascii")
        _LoopbackAdminAuthWrapper._token_file = token_file

        _LoopbackAdminAuthWrapper.rotate_token()

        assert _LoopbackAdminAuthWrapper._tokens == {"fresh-token"}

    def test_reload_ignores_empty_file(self, tmp_path):
        token_file = tmp_path / "orb-server.token"
        token_file.write_text("   ", encoding="ascii")  # whitespace only → empty
        _LoopbackAdminAuthWrapper._token_file = token_file
        _LoopbackAdminAuthWrapper._tokens = {"old"}

        _LoopbackAdminAuthWrapper.rotate_token()

        # Empty content must not clobber the existing token set.
        assert _LoopbackAdminAuthWrapper._tokens == {"old"}

    def test_reload_missing_file_is_silent(self, tmp_path):
        missing = tmp_path / "does-not-exist.token"
        _LoopbackAdminAuthWrapper._token_file = missing
        # Reload of a missing file must be swallowed (OSError → debug log).
        _LoopbackAdminAuthWrapper.rotate_token()

    @pytest.mark.asyncio
    async def test_check_and_refresh_reloads_on_mtime_change(self, tmp_path):
        token_file = tmp_path / "orb-server.token"
        token_file.write_text("token-v1", encoding="ascii")
        _LoopbackAdminAuthWrapper._token_file = token_file
        _LoopbackAdminAuthWrapper._token_file_mtime = 0.0  # force "changed"
        _LoopbackAdminAuthWrapper._tokens = {"token-v1"}

        # Rewrite with a new value; the per-request mtime check must pick it up.
        token_file.write_text("token-v2", encoding="ascii")

        inner = MagicMock()
        inner.authenticate = AsyncMock(return_value=AuthResult(status=AuthStatus.INVALID))
        wrapper = _LoopbackAdminAuthWrapper(inner)
        result = await wrapper.authenticate(_ctx("Bearer token-v2"))

        assert result.status == AuthStatus.SUCCESS
        assert _LoopbackAdminAuthWrapper._tokens == {"token-v2"}

    def test_check_and_refresh_noop_when_no_file(self):
        _LoopbackAdminAuthWrapper._token_file = None
        # Should return immediately without error.
        _LoopbackAdminAuthWrapper._check_and_refresh()


@pytest.mark.unit
@pytest.mark.api
class TestLoopbackTokenMiddleware:
    async def _run_dispatch(
        self,
        auth_header: str,
        client_host: str | None = "127.0.0.1",
        forwarded_for: str | None = None,
    ):
        """Invoke the middleware dispatch and return the (request, called) pair.

        ``client_host`` models ``request.client.host`` — the direct TCP peer
        address as seen by the ASGI server. Pass ``None`` to simulate
        ``request.client`` being unset entirely (e.g. certain test clients or
        unusual transports). ``forwarded_for`` is included in headers to prove
        it is never consulted for the loopback decision.
        """
        headers = {"authorization": auth_header}
        if forwarded_for is not None:
            headers["x-forwarded-for"] = forwarded_for
        request = SimpleNamespace(
            headers=headers,
            state=SimpleNamespace(),
            client=(SimpleNamespace(host=client_host) if client_host is not None else None),
        )
        called = {"next": False}

        async def _call_next(req):
            called["next"] = True
            return "response"

        result = await _LoopbackAdminTokenMiddleware._dispatch(request, _call_next)
        return request, called, result

    @pytest.mark.asyncio
    async def test_valid_token_stamps_admin_state(self):
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        request, called, result = await self._run_dispatch(
            "Bearer secret-token", client_host="127.0.0.1"
        )
        assert request.state.user_id == "loopback-admin"
        assert request.state.user_roles == ["admin"]
        assert request.state.permissions == ["*"]
        assert called["next"] is True
        assert result == "response"

    @pytest.mark.asyncio
    async def test_valid_token_from_ipv6_loopback_stamps_admin_state(self):
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        request, called, _ = await self._run_dispatch("Bearer secret-token", client_host="::1")
        assert request.state.user_id == "loopback-admin"
        assert called["next"] is True

    @pytest.mark.asyncio
    async def test_valid_token_from_ipv4_mapped_ipv6_loopback_stamps_admin_state(self):
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        request, called, _ = await self._run_dispatch(
            "Bearer secret-token", client_host="::ffff:127.0.0.1"
        )
        assert request.state.user_id == "loopback-admin"
        assert called["next"] is True

    @pytest.mark.asyncio
    async def test_valid_token_from_non_loopback_peer_is_rejected(self):
        """A correct token from a non-loopback peer must never grant admin."""
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        request, called, _ = await self._run_dispatch(
            "Bearer secret-token", client_host="203.0.113.5"
        )
        assert not hasattr(request.state, "user_id")
        assert called["next"] is True

    @pytest.mark.asyncio
    async def test_valid_token_with_no_client_is_rejected(self):
        """``request.client`` being unset must never be treated as loopback."""
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        request, called, _ = await self._run_dispatch("Bearer secret-token", client_host=None)
        assert not hasattr(request.state, "user_id")
        assert called["next"] is True

    @pytest.mark.asyncio
    async def test_valid_token_ignores_forwarded_for_from_non_loopback_peer(self):
        """X-Forwarded-For claiming loopback must not override the real peer."""
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        request, called, _ = await self._run_dispatch(
            "Bearer secret-token",
            client_host="203.0.113.5",
            forwarded_for="127.0.0.1",
        )
        assert not hasattr(request.state, "user_id")
        assert called["next"] is True

    @pytest.mark.asyncio
    async def test_non_matching_token_leaves_state_untouched(self):
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        request, called, _ = await self._run_dispatch("Bearer wrong")
        assert not hasattr(request.state, "user_id")
        assert called["next"] is True

    @pytest.mark.asyncio
    async def test_no_auth_header_falls_through(self):
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        request, called, _ = await self._run_dispatch("")
        assert not hasattr(request.state, "user_id")
        assert called["next"] is True

    @pytest.mark.asyncio
    async def test_non_ascii_token_falls_through_without_stamp(self):
        _LoopbackAdminAuthWrapper._tokens = {"secret-token"}
        request, called, _ = await self._run_dispatch("Bearer ünïcödé")
        # Non-ASCII can never match the secret; state must not be elevated.
        assert not hasattr(request.state, "user_id")
        assert called["next"] is True


@pytest.mark.unit
@pytest.mark.api
class TestLoadLoopbackToken:
    def test_loads_token_from_sibling_of_pid_file(self, tmp_path):
        pid_file = tmp_path / "orb-server.pid"
        token_file = tmp_path / "orb-server.token"
        token_file.write_text("daemon-token", encoding="ascii")
        server_config = SimpleNamespace(pid_file=str(pid_file))

        _load_loopback_token(server_config)

        assert _LoopbackAdminAuthWrapper._tokens == {"daemon-token"}
        assert _LoopbackAdminAuthWrapper._token_file == token_file

    def test_missing_token_file_is_silent(self, tmp_path):
        pid_file = tmp_path / "orb-server.pid"  # no sibling .token file created
        server_config = SimpleNamespace(pid_file=str(pid_file))

        _load_loopback_token(server_config)

        # No token registered; call must not raise.
        assert _LoopbackAdminAuthWrapper._tokens == set()

    def test_empty_token_file_registers_nothing(self, tmp_path):
        pid_file = tmp_path / "orb-server.pid"
        (tmp_path / "orb-server.token").write_text("  ", encoding="ascii")
        server_config = SimpleNamespace(pid_file=str(pid_file))

        _load_loopback_token(server_config)

        assert _LoopbackAdminAuthWrapper._tokens == set()

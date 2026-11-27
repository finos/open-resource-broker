"""Loopback-admin token machinery for the REST server.

The daemon writes a token file next to its PID file; a bearer token matching it
is honoured as an admin identity, but only when the direct TCP peer is loopback.
"""

import ipaddress
import os
import secrets
from pathlib import Path
from typing import Any, ClassVar

from orb.infrastructure.logging.logger import get_logger

# Log under the server's own logger name so existing log filters and alerts keep
# matching after the code moved out of orb.api.server.
_LOGGER_NAME = "orb.api.server"
_server_logger = get_logger(_LOGGER_NAME)


class _LoopbackAdminAuthWrapper:
    """Thin auth-port wrapper that accepts the loopback-admin token.

    When the daemon's loopback reload IPC sends ``Authorization: Bearer <token>``
    and that token matches the value written to ``orb-server.token``, this
    wrapper short-circuits normal JWT validation and grants an admin identity.
    For every other token it delegates to the real inner strategy unchanged.

    This keeps the loopback capability fully isolated: it never modifies the
    existing JWT strategy, and the token is only ever read from a file that is
    mode 0o600 (daemon-UID-only readable).

    The token set is stored as a class attribute so that it is tied to the class
    object rather than the module namespace.  This survives module reloads in
    test scenarios (where ``sys.modules["orb.api.server"]`` is mutated) because
    code that holds a reference to this class always reads the same set regardless
    of which module object ``orb.api.server`` currently points to.

    Token cache invalidation: the token file's mtime is checked on each request
    (cheap ``os.stat``).  When the mtime has changed since the last load the
    file is reloaded automatically.  The daemon (or any privileged caller) can
    also call ``rotate_token()`` on SIGHUP to force an immediate reload.
    """

    _tokens: ClassVar[set[str]] = set()
    # Path of the token file that was last loaded; None if tokens were
    # registered programmatically (e.g. in tests via _tokens.add()).
    _token_file: ClassVar[Path | None] = None
    # mtime of the token file at the time of the last successful load.
    _token_file_mtime: ClassVar[float] = 0.0

    @classmethod
    def rotate_token(cls) -> None:
        """Force an immediate reload of the token file.

        Intended to be called by the daemon on SIGHUP so that a rotated token
        becomes active without a full server restart.  If no token file is
        registered (auth disabled or fresh install) the call is a no-op.
        """
        if cls._token_file is None:
            return
        cls._reload_token_file(cls._token_file)

    @classmethod
    def _reload_token_file(cls, token_file: Path) -> None:
        """Load tokens from *token_file* and replace the current token set."""
        try:
            token = token_file.read_text(encoding="ascii").strip()
            if token:
                cls._tokens = {token}
                try:
                    cls._token_file_mtime = os.stat(token_file).st_mtime
                except OSError:
                    cls._token_file_mtime = 0.0
                _server_logger.info("loopback-admin token reloaded from %s", token_file)
        except OSError as exc:
            _server_logger.debug("loopback-admin token reload skipped: %s", exc)

    @classmethod
    def _check_and_refresh(cls) -> None:
        """Reload the token file if its mtime has changed since last load.

        Called on each authenticate() invocation — ``os.stat`` is a single
        syscall and is cheap enough for per-request use.
        """
        if cls._token_file is None:
            return
        try:
            current_mtime = os.stat(cls._token_file).st_mtime
        except OSError:
            return
        if current_mtime != cls._token_file_mtime:
            cls._reload_token_file(cls._token_file)

    def __init__(self, inner: Any) -> None:
        """Wrap the real auth strategy *inner*."""
        self._inner = inner
        self._logger = get_logger(_LOGGER_NAME)

    async def authenticate(self, context: Any) -> Any:
        from orb.infrastructure.adapters.ports.auth import AuthResult, AuthStatus

        # Cheap mtime check — reloads token file only when it has changed.
        self._check_and_refresh()

        auth_header: str = context.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            candidate = auth_header[7:].strip()
            try:
                candidate_bytes = candidate.encode("ascii") if candidate else b""
            except UnicodeEncodeError:
                # Non-ASCII bearer token can never match the loopback secret;
                # answer with an invalid-credentials result rather than crashing or
                # silently skipping the auth stamp (which could be an auth bypass).
                return AuthResult(
                    status=AuthStatus.INVALID,
                    user_id=None,
                    user_roles=[],
                    permissions=[],
                    error_message="invalid credentials",
                )
            if candidate_bytes and any(
                secrets.compare_digest(candidate_bytes, t.encode("ascii"))
                for t in _LoopbackAdminAuthWrapper._tokens
            ):
                # Mirror _LoopbackAdminTokenMiddleware below: the token value
                # alone is never enough. Only the direct TCP peer counts,
                # never X-Forwarded-For/other client-supplied headers, and
                # never context.client_ip (which can be resolved through a
                # trusted proxy forwarded header) so a remote caller cannot
                # spoof a loopback origin merely by presenting the token.
                # A missing peer is treated as non-loopback.
                if _is_loopback_address(getattr(context, "direct_client_ip", None)):
                    self._logger.debug("loopback-admin token accepted for %s", context.path)
                    return AuthResult(
                        status=AuthStatus.SUCCESS,
                        user_id="loopback-admin",
                        user_roles=["admin"],
                        permissions=["*"],
                        metadata={"strategy": "loopback_admin_token"},
                    )
                self._logger.warning(
                    "loopback-admin token presented from non-loopback peer for %s; "
                    "ignoring and falling through to normal auth",
                    context.path,
                )
        return await self._inner.authenticate(context)

    def get_strategy_name(self) -> str:
        """Return the wrapped strategy's name."""
        return self._inner.get_strategy_name()

    def is_enabled(self) -> bool:
        """Return whether the wrapped strategy is enabled."""
        return self._inner.is_enabled()

    # Delegate all other attribute access to the inner strategy.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _load_loopback_token(server_config: Any) -> None:
    """Read the daemon-written loopback-admin token file and register it.

    The token file path mirrors the PID file path: if the PID file is
    ``<work_dir>/server/orb-server.pid``, the token file is
    ``<work_dir>/server/orb-server.token``.

    Silently skips if the file does not exist (auth disabled, fresh install,
    or daemon not yet started).
    """
    try:
        from orb.config.platform_dirs import get_work_location

        pid_file = getattr(server_config, "pid_file", None) or str(
            get_work_location() / "server" / "orb-server.pid"
        )
        token_file = Path(pid_file).with_name(Path(pid_file).stem + ".token")
        if token_file.exists():
            token = token_file.read_text(encoding="ascii").strip()
            if token:
                _LoopbackAdminAuthWrapper._tokens = {token}
                _LoopbackAdminAuthWrapper._token_file = token_file
                try:
                    _LoopbackAdminAuthWrapper._token_file_mtime = os.stat(token_file).st_mtime
                except OSError:
                    _LoopbackAdminAuthWrapper._token_file_mtime = 0.0
                _server_logger.debug("loopback-admin token loaded from %s", token_file)
    except Exception as exc:
        _server_logger.debug("loopback-admin token load skipped: %s", exc)


def _is_loopback_address(host: str | None) -> bool:
    """Return True only when *host* is a loopback IP address literal.

    Shared by every call site that needs to decide whether a request
    originated on the local host. Callers must pass the *direct* socket
    peer address — never a value derived from ``X-Forwarded-For`` or any
    other client-supplied header — or this check can be spoofed by anyone
    who can reach the server over the network. Accepts IPv4 loopback
    (127.0.0.0/8), IPv6 loopback (``::1``), and IPv4-mapped IPv6 loopback
    (``::ffff:127.0.0.1``).
    """
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return addr.is_loopback


def _is_loopback_peer(request: Any) -> bool:
    """Return True only when the request's direct TCP peer is loopback.

    Deliberately reads only ``request.client`` — the actual socket peer
    address as seen by the ASGI server — and never consults
    ``X-Forwarded-For`` or any other client-supplied header. Those headers
    can be set to any value by anyone who can reach the server over the
    network, so trusting them here would defeat the whole point of this
    check.
    """
    client = request.client
    return _is_loopback_address(client.host if client else None)


class _LoopbackAdminTokenMiddleware:
    """Always-on middleware that stamps admin identity for valid loopback tokens.

    Runs regardless of whether the primary auth middleware is enabled.  When
    the request carries ``Authorization: Bearer <token>`` and that token
    matches the value the daemon wrote to ``<work_dir>/server/orb-server.token``
    at startup, the middleware stamps ``request.state`` with the admin role so
    role-guarded routes (POST /machines/request, /admin/*, etc.) accept the
    call.

    Fall-through (no header, or non-matching token) leaves request state
    untouched so the ``AuthMiddleware`` (when present) and ``get_current_user``
    dependency continue to behave as before.
    """

    def __init__(self, app: Any) -> None:
        """Wrap *app* in a Starlette base middleware that dispatches to ``_dispatch``."""
        from starlette.middleware.base import BaseHTTPMiddleware

        self._impl = BaseHTTPMiddleware(app, dispatch=self._dispatch)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self._impl(scope, receive, send)

    @staticmethod
    async def _dispatch(request: Any, call_next: Any) -> Any:
        # Cheap mtime check so the token file is reloaded when rotated.
        _LoopbackAdminAuthWrapper._check_and_refresh()

        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            candidate = auth[7:].strip()
            try:
                candidate_bytes = candidate.encode("ascii") if candidate else b""
            except UnicodeEncodeError:
                # Non-ASCII bearer value can never match the loopback secret;
                # skip the stamp so the request is not erroneously elevated.
                return await call_next(request)
            if candidate_bytes and any(
                secrets.compare_digest(candidate_bytes, t.encode("ascii"))
                for t in _LoopbackAdminAuthWrapper._tokens
            ):
                if _is_loopback_peer(request):
                    request.state.user_id = "loopback-admin"
                    request.state.user_roles = ["admin"]
                    request.state.permissions = ["*"]
                else:
                    # Token value matched, but the request did not originate
                    # from the local host. Never honour it from a
                    # non-loopback peer, and never trust X-Forwarded-For (or
                    # any other client-supplied header) for this decision —
                    # fall through to normal auth instead. Never log the
                    # token value itself.
                    client = request.client
                    _server_logger.warning(
                        "loopback-admin token presented from non-loopback peer "
                        "%s; ignoring and falling through to normal auth",
                        client.host if client else "<no client>",
                    )
        return await call_next(request)

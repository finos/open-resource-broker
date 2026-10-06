"""H11 — Tests for _loopback_token() and _headers() in orb.ui.api_http.

Cases:
  (a) token file present + readable → Authorization: Bearer <token>
  (b) file absent → no Authorization header, other headers preserved
  (c) file present but empty → no Authorization header
  (d) ORB_LOOPBACK_TOKEN_FILE env override takes precedence over default path
  (e) OSError on read → falls through to next candidate, eventually returns None

All tests patch at the filesystem level (tmp_path) or via monkeypatch of
the env var, so they do not require a running ORB daemon.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_api_http():
    """Import the api_http module.  No reflex dependency; safe to import directly."""
    import orb.ui.api_http as mod

    return mod


# ---------------------------------------------------------------------------
# _loopback_token
# ---------------------------------------------------------------------------


class TestLoopbackToken:
    """Exercises the _loopback_token discovery function."""

    def test_reads_token_from_file_when_present(self, tmp_path: Path, monkeypatch):
        """(a) File present and readable → token string returned."""
        mod = _import_api_http()

        token_file = tmp_path / "orb-server.token"
        token_file.write_text("my-secret-token", encoding="ascii")

        # Patch get_work_location to return a directory whose
        # ``server/orb-server.token`` is the file we just created.
        # The function looks up: get_work_location() / "server" / "orb-server.token"
        work_dir = tmp_path
        # We need the full path: tmp_path/server/orb-server.token
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        token_file2 = server_dir / "orb-server.token"
        token_file2.write_text("my-secret-token", encoding="ascii")

        monkeypatch.delenv("ORB_LOOPBACK_TOKEN_FILE", raising=False)

        # Exercise the fully-mocked path first (proves the wrapper
        # doesn't accidentally raise when the underlying function is
        # patched).  Discarding the return value keeps CodeQL happy —
        # the assertion runs against the real code path below.
        with patch.object(mod, "_loopback_token", wraps=mod._loopback_token):
            with patch("orb.ui.api_http._loopback_token") as mock_fn:
                mock_fn.return_value = "my-secret-token"
                mod._loopback_token()

        # Now exercise the real code path with ``get_work_location``
        # pointing at the tmp_path fixture, so the token is actually
        # read from disk.
        import orb.config.platform_dirs as pd_mod

        with patch.object(pd_mod, "get_work_location", return_value=work_dir):
            result = mod._loopback_token()

        assert result == "my-secret-token"

    def test_returns_none_when_file_absent(self, tmp_path: Path, monkeypatch):
        """(b) File absent → None returned (no Authorization set)."""
        mod = _import_api_http()

        monkeypatch.delenv("ORB_LOOPBACK_TOKEN_FILE", raising=False)
        work_dir = tmp_path  # server/orb-server.token does NOT exist here

        import orb.config.platform_dirs as pd_mod

        with patch.object(pd_mod, "get_work_location", return_value=work_dir):
            result = mod._loopback_token()

        assert result is None

    def test_returns_none_when_file_empty(self, tmp_path: Path, monkeypatch):
        """(c) File present but empty (whitespace-only) → None."""
        mod = _import_api_http()

        monkeypatch.delenv("ORB_LOOPBACK_TOKEN_FILE", raising=False)
        work_dir = tmp_path
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        (server_dir / "orb-server.token").write_text("   \n", encoding="ascii")

        import orb.config.platform_dirs as pd_mod

        with patch.object(pd_mod, "get_work_location", return_value=work_dir):
            result = mod._loopback_token()

        assert result is None

    def test_env_override_takes_precedence(self, tmp_path: Path, monkeypatch):
        """(d) ORB_LOOPBACK_TOKEN_FILE env var takes precedence over default path."""
        mod = _import_api_http()

        # The env-override file has a different token.
        override_file = tmp_path / "override.token"
        override_file.write_text("env-override-token", encoding="ascii")

        monkeypatch.setenv("ORB_LOOPBACK_TOKEN_FILE", str(override_file))

        # Default path also exists but should NOT be consulted.
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        (server_dir / "orb-server.token").write_text("default-token", encoding="ascii")

        import orb.config.platform_dirs as pd_mod

        with patch.object(pd_mod, "get_work_location", return_value=tmp_path):
            result = mod._loopback_token()

        assert result == "env-override-token"

    def test_oserror_on_read_falls_through(self, tmp_path: Path, monkeypatch):
        """(e) OSError when reading a candidate → continue to next, return None if all fail."""
        mod = _import_api_http()

        monkeypatch.delenv("ORB_LOOPBACK_TOKEN_FILE", raising=False)

        work_dir = tmp_path
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        # Create the file but make is_file() return True while read_text raises OSError
        token_path = server_dir / "orb-server.token"
        token_path.write_text("some-token", encoding="ascii")

        original_read_text = Path.read_text

        def _raise_oserror(self, *args, **kwargs):
            if self.name == "orb-server.token":
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        import orb.config.platform_dirs as pd_mod

        with patch.object(pd_mod, "get_work_location", return_value=work_dir):
            with patch.object(Path, "read_text", _raise_oserror):
                result = mod._loopback_token()

        assert result is None


# ---------------------------------------------------------------------------
# _headers
# ---------------------------------------------------------------------------


class TestHeaders:
    """Exercises _headers() — the dict sent on every httpx request.

    ``_headers()`` only attaches the loopback-admin token when
    ``_auth_enabled()`` reports ``False`` (operator has not configured a
    real auth strategy). All cases here patch ``_auth_enabled`` directly to
    isolate the gating behaviour from config resolution, which is covered
    separately in ``TestAuthEnabled`` below.
    """

    def test_includes_authorization_when_token_present_and_auth_disabled(self):
        """Token present + auth disabled → Authorization: Bearer <token> is in headers."""
        mod = _import_api_http()

        with (
            patch.object(mod, "_auth_enabled", return_value=False),
            patch.object(mod, "_loopback_token", return_value="test-token-xyz"),
        ):
            headers = mod._headers()

        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test-token-xyz"

    def test_excludes_authorization_when_no_token(self):
        """No token → Authorization key absent from headers."""
        mod = _import_api_http()

        with (
            patch.object(mod, "_auth_enabled", return_value=False),
            patch.object(mod, "_loopback_token", return_value=None),
        ):
            headers = mod._headers()

        assert "Authorization" not in headers

    def test_excludes_authorization_when_auth_enabled_even_with_token(self):
        """Auth enabled → Authorization is withheld even though a valid token exists.

        This is the regression guard for the dashboard self-escalation bug:
        the loopback-admin token must never be forwarded once the operator
        has configured real authentication.
        """
        mod = _import_api_http()

        with (
            patch.object(mod, "_auth_enabled", return_value=True),
            patch.object(mod, "_loopback_token", return_value="test-token-xyz"),
        ):
            headers = mod._headers()

        assert "Authorization" not in headers

    def test_preserves_default_headers_when_no_token(self):
        """Other default headers (e.g. X-ORB-Scheduler) survive when no token."""
        mod = _import_api_http()

        with (
            patch.object(mod, "_auth_enabled", return_value=False),
            patch.object(mod, "_loopback_token", return_value=None),
        ):
            headers = mod._headers()

        # _DEFAULT_HEADERS always includes X-ORB-Scheduler
        assert "X-ORB-Scheduler" in headers

    def test_preserves_default_headers_when_token_present(self):
        """Both Authorization and default headers coexist."""
        mod = _import_api_http()

        with (
            patch.object(mod, "_auth_enabled", return_value=False),
            patch.object(mod, "_loopback_token", return_value="tok"),
        ):
            headers = mod._headers()

        assert "X-ORB-Scheduler" in headers
        assert "Authorization" in headers


# ---------------------------------------------------------------------------
# _auth_enabled
# ---------------------------------------------------------------------------


class TestAuthEnabled:
    """Exercises _auth_enabled() against real configuration resolution.

    ORB_CONFIG_DIR points the default config discovery at a temp directory,
    so the real ConfigurationManager and ServerConfig schema are used.
    """

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path: Path, monkeypatch):
        mod = _import_api_http()
        monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))
        mod._auth_enabled_cache.clear()
        self.mod = mod
        self.config_dir = tmp_path
        yield
        mod._auth_enabled_cache.clear()

    def _write_config(self, auth: dict) -> None:
        (self.config_dir / "config.json").write_text(json.dumps({"server": {"auth": auth}}))

    def test_returns_true_when_configured_enabled(self):
        self._write_config({"enabled": True, "strategy": "bearer_token"})
        assert self.mod._auth_enabled() is True

    def test_returns_false_when_configured_disabled(self):
        self._write_config({"enabled": False, "strategy": "none"})
        assert self.mod._auth_enabled() is False

    def test_missing_config_matches_server_default(self):
        """No config file: same value the server's own ServerConfig default yields."""
        from orb.config.schemas.server_schema import ServerConfig

        assert self.mod._auth_enabled() is ServerConfig().auth.enabled

    def test_result_is_cached_within_ttl(self):
        self._write_config({"enabled": False, "strategy": "none"})
        assert self.mod._auth_enabled() is False

        self._write_config({"enabled": True, "strategy": "bearer_token"})
        assert self.mod._auth_enabled() is False

    def test_config_is_resolved_once_within_ttl_and_again_after(self, monkeypatch):
        from orb.config.managers import configuration_manager as cm

        clock = [1000.0]
        monkeypatch.setattr(self.mod.time, "monotonic", lambda: clock[0])
        calls = []
        real = cm.ConfigurationManager

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(cm, "ConfigurationManager", counting)
        self._write_config({"enabled": False, "strategy": "none"})

        assert self.mod._auth_enabled() is False
        clock[0] += self.mod._AUTH_ENABLED_TTL_SECONDS - 1
        assert self.mod._auth_enabled() is False
        assert len(calls) == 1

        self._write_config({"enabled": True, "strategy": "bearer_token"})
        clock[0] += 2
        assert self.mod._auth_enabled() is True
        assert len(calls) == 2

    def test_value_is_refreshed_after_ttl(self):
        self._write_config({"enabled": False, "strategy": "none"})
        assert self.mod._auth_enabled() is False

        self._write_config({"enabled": True, "strategy": "bearer_token"})
        self.mod._auth_enabled_cache.expiry = 0.0
        assert self.mod._auth_enabled() is True

    def test_resolution_failure_is_logged_and_fails_safe(self):
        with (
            patch(
                "orb.config.managers.configuration_manager.ConfigurationManager",
                side_effect=RuntimeError("config boom"),
            ),
            patch("orb.infrastructure.logging.logger.get_logger") as get_logger,
        ):
            result = self.mod._auth_enabled()

        assert result is True
        get_logger.return_value.warning.assert_called_once()
        assert "config boom" in str(get_logger.return_value.warning.call_args)

    def test_resolution_failure_is_retried_and_recovers(self):
        with patch(
            "orb.config.managers.configuration_manager.ConfigurationManager",
            side_effect=RuntimeError("config boom"),
        ):
            assert self.mod._auth_enabled() is True

        # The failure is cached only for the short failure TTL, far below the
        # success TTL, so a later call re-resolves instead of sticking at True.
        assert self.mod._AUTH_ENABLED_FAILURE_TTL_SECONDS < self.mod._AUTH_ENABLED_TTL_SECONDS
        self._write_config({"enabled": False, "strategy": "none"})
        self.mod._auth_enabled_cache.expiry = 0.0
        assert self.mod._auth_enabled() is False

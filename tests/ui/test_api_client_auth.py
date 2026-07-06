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

from pathlib import Path
from unittest.mock import patch

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
    """Exercises _headers() — the dict sent on every httpx request."""

    def test_includes_authorization_when_token_present(self):
        """Token present → Authorization: Bearer <token> is in headers."""
        mod = _import_api_http()

        with patch.object(mod, "_loopback_token", return_value="test-token-xyz"):
            headers = mod._headers()

        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test-token-xyz"

    def test_excludes_authorization_when_no_token(self):
        """No token → Authorization key absent from headers."""
        mod = _import_api_http()

        with patch.object(mod, "_loopback_token", return_value=None):
            headers = mod._headers()

        assert "Authorization" not in headers

    def test_preserves_default_headers_when_no_token(self):
        """Other default headers (e.g. X-ORB-Scheduler) survive when no token."""
        mod = _import_api_http()

        with patch.object(mod, "_loopback_token", return_value=None):
            headers = mod._headers()

        # _DEFAULT_HEADERS always includes X-ORB-Scheduler
        assert "X-ORB-Scheduler" in headers

    def test_preserves_default_headers_when_token_present(self):
        """Both Authorization and default headers coexist."""
        mod = _import_api_http()

        with patch.object(mod, "_loopback_token", return_value="tok"):
            headers = mod._headers()

        assert "X-ORB-Scheduler" in headers
        assert "Authorization" in headers

"""Tests for POST /config/save path-traversal rejection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import CurrentUser, get_config_manager, get_current_user
from orb.api.routers.config import router as config_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG_DIR = "/etc/orb"


def _make_config_port(*, config_dir: str = _CONFIG_DIR) -> MagicMock:
    """Return a minimal ConfigurationPort mock."""
    port = MagicMock()
    port.get_config_dir.return_value = config_dir
    port.save_config.return_value = f"{config_dir}/orb.yaml"
    port.get_configuration_sources.return_value = {}
    port.validate_configuration.return_value = []
    return port


def _make_app(port: MagicMock) -> FastAPI:
    """Return a FastAPI test app with auth and DI overrides applied."""
    app = FastAPI()
    app.include_router(config_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-admin", role="admin"
    )
    app.dependency_overrides[get_config_manager] = lambda: port
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestSaveConfigPathTraversal:
    """POST /config/save rejects paths outside the configured config directory."""

    def _client(self, config_dir: str = _CONFIG_DIR) -> tuple[TestClient, MagicMock]:
        port = _make_config_port(config_dir=config_dir)
        app = _make_app(port)
        # Neutralise the destructive-admin guard; the traversal check is what we test.
        with patch("orb.api.routers.config._check_destructive_admin_allowed", return_value=None):
            client = TestClient(app, raise_server_exceptions=False)
        return client, port

    def test_absolute_path_outside_config_dir_returns_400(self):
        """/etc/crontab is outside /etc/orb — must return HTTP 400."""
        port = _make_config_port(config_dir=_CONFIG_DIR)
        app = _make_app(port)

        with patch("orb.api.routers.config._check_destructive_admin_allowed", return_value=None):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/config/save", json={"path": "/etc/crontab"})

        assert r.status_code == 400
        body = r.json()
        assert body["detail"]["code"] == "PATH_OUTSIDE_CONFIG_DIR"
        assert body["detail"]["message"] == "path outside config directory"
        # Config root must not appear in the error message (no path leakage).
        assert _CONFIG_DIR not in body["detail"]["message"]
        port.save_config.assert_not_called()

    def test_root_ssh_authorized_keys_returns_400(self):
        """/root/.ssh/authorized_keys is outside config dir — must return HTTP 400."""
        port = _make_config_port(config_dir=_CONFIG_DIR)
        app = _make_app(port)

        with patch("orb.api.routers.config._check_destructive_admin_allowed", return_value=None):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/config/save", json={"path": "/root/.ssh/authorized_keys"})

        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "PATH_OUTSIDE_CONFIG_DIR"
        assert r.json()["detail"]["message"] == "path outside config directory"
        port.save_config.assert_not_called()

    def test_relative_traversal_path_returns_400(self):
        """../secrets traverses outside config dir — must return HTTP 400."""
        port = _make_config_port(config_dir=_CONFIG_DIR)
        app = _make_app(port)

        with patch("orb.api.routers.config._check_destructive_admin_allowed", return_value=None):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/config/save", json={"path": "../secrets"})

        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "PATH_OUTSIDE_CONFIG_DIR"
        port.save_config.assert_not_called()

    def test_valid_path_inside_config_dir_succeeds(self):
        """A path inside the config directory is accepted and save_config is called with the resolved path."""
        from pathlib import Path as _Path

        config_dir = "/etc/orb"
        valid_path = f"{config_dir}/custom.yaml"
        # save_config is invoked with the fully-resolved path (TOCTOU fix).
        resolved_valid_path = str(_Path(valid_path).resolve())
        port = _make_config_port(config_dir=config_dir)
        port.save_config.return_value = resolved_valid_path
        app = _make_app(port)

        with patch("orb.api.routers.config._check_destructive_admin_allowed", return_value=None):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/config/save", json={"path": valid_path})

        assert r.status_code == 200
        body = r.json()
        assert body["persisted"] is True
        assert body["path"] == resolved_valid_path
        port.save_config.assert_called_once_with(resolved_valid_path)

    def test_no_path_body_uses_default_save(self):
        """When body.path is omitted the traversal check is skipped and save proceeds."""
        port = _make_config_port()
        port.save_config.return_value = f"{_CONFIG_DIR}/orb.yaml"
        app = _make_app(port)

        with patch("orb.api.routers.config._check_destructive_admin_allowed", return_value=None):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/config/save", json={})

        assert r.status_code == 200
        port.save_config.assert_called_once_with(None)

    def test_error_message_does_not_leak_config_root(self):
        """The 400 error message must not contain the resolved config root path."""
        config_dir = "/very/secret/internal/path"
        port = _make_config_port(config_dir=config_dir)
        app = _make_app(port)

        with patch("orb.api.routers.config._check_destructive_admin_allowed", return_value=None):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/config/save", json={"path": "/etc/passwd"})

        assert r.status_code == 400
        detail = r.json()["detail"]
        assert config_dir not in detail["message"]
        assert "/very/secret" not in str(detail)

    def test_null_byte_in_path_returns_400(self):
        """A path containing a null byte must return HTTP 400 (INVALID_PATH), not bypass the check."""
        port = _make_config_port(config_dir=_CONFIG_DIR)
        app = _make_app(port)

        with patch("orb.api.routers.config._check_destructive_admin_allowed", return_value=None):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/config/save", json={"path": "/etc/foo\x00.txt"})

        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["code"] == "INVALID_PATH"
        assert detail["message"] == "invalid path"
        port.save_config.assert_not_called()

    def test_broken_symlink_path_returns_400(self, tmp_path):
        """A broken symlink as the target path must return HTTP 400, not bypass the containment check."""
        import os

        # Create a symlink inside a config dir that points to a nonexistent target.
        config_dir = str(tmp_path / "config")
        os.makedirs(config_dir)
        broken_link = os.path.join(config_dir, "missing.yaml")
        os.symlink("/nonexistent/target/file.yaml", broken_link)

        port = _make_config_port(config_dir=config_dir)
        # get_config_dir also needs to resolve; make the config_root resolution succeed
        # but force the target path resolution to fail by patching Path.resolve.
        app = _make_app(port)

        original_resolve = __import__("pathlib").Path.resolve

        call_count = [0]

        def selective_resolve(self, strict=False):
            call_count[0] += 1
            # First call is for config_root; second is for the target.
            if call_count[0] == 1:
                return original_resolve(self, strict=False)
            # Simulate resolution failure for the broken symlink target.
            raise OSError("No such file or directory")

        with (
            patch("orb.api.routers.config._check_destructive_admin_allowed", return_value=None),
            patch("orb.api.routers.config.Path.resolve", selective_resolve),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/config/save", json={"path": broken_link})

        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["code"] == "INVALID_PATH"
        assert detail["message"] == "invalid path"
        port.save_config.assert_not_called()

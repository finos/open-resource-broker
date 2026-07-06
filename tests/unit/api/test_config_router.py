"""Unit tests for the config router — /api/v1/config/*."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import CurrentUser, get_config_manager, get_current_user
from orb.api.routers.config import router as config_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config_app():
    """Minimal FastAPI app with only the config router mounted.

    Overrides ``get_current_user`` to return an admin identity so the
    ``require_role("admin")`` guards on all config GET endpoints are satisfied.
    """
    app = FastAPI()
    app.include_router(config_router)
    # Supply a synthetic admin identity so role guards never interfere.
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-admin", role="admin"
    )
    return app


def _make_config_port(
    *,
    config_dict: dict | None = None,
    sources: dict | None = None,
    loaded_file: str | None = "/etc/orb/orb.yaml",
    validation_errors: list | None = None,
):
    """Return a MagicMock ConfigurationPort with the given settings."""
    port = MagicMock()

    _config = config_dict or {
        "server": {"host": "0.0.0.0", "port": 8000},
        "storage": {"backend": "sqlite"},
    }
    _sources = (
        sources
        if sources is not None
        else {
            "config_file": loaded_file,
            "config_dir": "/etc/orb",
            "primary_source": "config_file",
        }
    )

    port.get_app_config.return_value = _config
    port.get_configuration_sources.return_value = _sources
    port.get_loaded_config_file.return_value = loaded_file
    port.validate_configuration.return_value = validation_errors or []

    def _get_value(key, default=None):
        # Traverse dot-notation into _config
        parts = key.split(".")
        node = _config
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    port.get_configuration_value.side_effect = _get_value
    port.set_configuration_value.return_value = None

    return port


def _client_with_port(app: FastAPI, port) -> TestClient:
    """Return a TestClient with the config port dependency overridden."""
    app.dependency_overrides[get_config_manager] = lambda: port
    try:
        return TestClient(app, raise_server_exceptions=False)
    finally:
        # overrides persist on the app object; each test should be isolated
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestGetFullConfig:
    """Tests for GET /config/."""

    def test_returns_200_with_config_dict(self, config_app):
        """GET /config/ returns the full config dict with 200."""
        port = _make_config_port()

        with patch("orb.api.routers.config.get_config_manager", return_value=port):
            client = TestClient(config_app)
            r = client.get("/config/")

        assert r.status_code == 200
        body = r.json()
        assert "server" in body
        assert body["server"]["port"] == 8000

    def test_returns_empty_dict_on_attribute_error(self, config_app):
        """When get_app_config raises AttributeError, returns empty dict gracefully."""
        port = MagicMock()
        port.get_app_config.side_effect = AttributeError("no get_app_config")

        client = _client_with_port(config_app, port)
        r = client.get("/config/")

        assert r.status_code == 200
        assert r.json() == {}


@pytest.mark.unit
@pytest.mark.api
class TestGetConfigValue:
    """Tests for GET /config/{key}."""

    def test_returns_value_for_existing_key(self, config_app):
        """GET /config/server.port returns the value for a dot-notation key."""
        port = _make_config_port()

        with patch("orb.api.routers.config.get_config_manager", return_value=port):
            client = TestClient(config_app)
            r = client.get("/config/server.port")

        assert r.status_code == 200
        body = r.json()
        assert body["key"] == "server.port"
        assert body["value"] == 8000

    def test_returns_404_for_missing_key(self, config_app):
        """GET /config/{key} returns 404 when the key is not found."""
        port = _make_config_port()
        # Override so missing key returns the sentinel (no-op; default handles it)

        with patch("orb.api.routers.config.get_config_manager", return_value=port):
            client = TestClient(config_app, raise_server_exceptions=False)
            r = client.get("/config/nonexistent.key")

        assert r.status_code == 404
        body = r.json()
        assert body["detail"]["code"] == "CONFIG_KEY_NOT_FOUND"

    def test_returns_top_level_value(self, config_app):
        """GET /config/storage returns the nested storage section."""
        port = _make_config_port()

        client = _client_with_port(config_app, port)
        r = client.get("/config/storage")

        assert r.status_code == 200
        assert r.json()["value"] == {"backend": "sqlite"}


@pytest.mark.unit
@pytest.mark.api
class TestSetConfigValue:
    """Tests for PUT /config/{key}."""

    def test_happy_path_sets_value_and_returns_persisted_false(self, config_app):
        """PUT /config/{key} sets in-memory value and returns persisted=false."""
        port = _make_config_port()

        client = _client_with_port(config_app, port)
        r = client.put("/config/server.port", json={"value": 9090})

        assert r.status_code == 200
        body = r.json()
        assert body["persisted"] is False
        assert "note" in body
        # Confirm set_configuration_value was called
        port.set_configuration_value.assert_called_once_with("server.port", 9090)

    def test_returns_400_on_missing_body(self, config_app):
        """PUT /config/{key} with no body returns 422 (unprocessable)."""
        port = _make_config_port()

        with patch("orb.api.routers.config.get_config_manager", return_value=port):
            client = TestClient(config_app, raise_server_exceptions=False)
            r = client.put("/config/server.port")

        # FastAPI/pydantic returns 422 for missing required body
        assert r.status_code == 422

    def test_set_string_value(self, config_app):
        """PUT /config/{key} with a string value works correctly."""
        port = _make_config_port()

        client = _client_with_port(config_app, port)
        r = client.put("/config/storage.backend", json={"value": "postgres"})

        assert r.status_code == 200
        port.set_configuration_value.assert_called_once_with("storage.backend", "postgres")

    def test_note_contains_in_memory_warning(self, config_app):
        """Response note warns that the change is in-memory only."""
        port = _make_config_port()

        with patch("orb.api.routers.config.get_config_manager", return_value=port):
            client = TestClient(config_app)
            r = client.put("/config/server.port", json={"value": 9090})

        body = r.json()
        assert "in-memory" in body["note"].lower() or "revert" in body["note"].lower()


@pytest.mark.unit
@pytest.mark.api
class TestGetConfigSources:
    """Tests for GET /config/sources."""

    def test_returns_sources_dict(self, config_app):
        """GET /config/sources returns the sources dict from the port."""
        port = _make_config_port()

        client = _client_with_port(config_app, port)
        r = client.get("/config/sources")

        assert r.status_code == 200
        body = r.json()
        assert "config_file" in body
        assert body["primary_source"] == "config_file"

    def test_returns_empty_when_sources_empty(self, config_app):
        """GET /config/sources with empty sources returns empty dict."""
        port = _make_config_port(sources={})

        client = _client_with_port(config_app, port)
        r = client.get("/config/sources")

        assert r.status_code == 200
        assert r.json() == {}


# ---------------------------------------------------------------------------
# Tests for executor offload — config file reads don't block the event loop
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
@pytest.mark.asyncio
class TestConfigExecutorOffload:
    """Verify that the fallback open() in GET /config/?source=file is offloaded
    to a thread-pool executor so concurrent requests are not stalled."""

    async def test_config_file_read_uses_run_in_executor(self, config_app, tmp_path):
        """The blocking file read is executed via run_in_executor, not inline."""
        import json

        # Write a real config file so the read succeeds.
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"server": {"port": 8000}}))

        executor_calls: list[tuple] = []

        async def fake_run_in_executor(executor, fn, *args):
            executor_calls.append((executor, fn, args))
            # Still run fn so the endpoint gets a valid result.
            return fn(*args)

        mock_loop = MagicMock()
        mock_loop.run_in_executor = fake_run_in_executor

        # Build a config manager that lacks get_raw_config (falls back to open())
        # and provides the tmp file path.
        port = MagicMock()
        port.get_raw_config.side_effect = AttributeError("no get_raw_config")
        port.get_loaded_config_file.return_value = str(cfg_file)

        from httpx import ASGITransport, AsyncClient

        config_app.dependency_overrides[get_config_manager] = lambda: port

        with patch("orb.api.routers.config.asyncio.get_running_loop", return_value=mock_loop):
            async with AsyncClient(
                transport=ASGITransport(app=config_app), base_url="http://test"
            ) as ac:
                r = await ac.get("/config/?source=file")

        assert r.status_code == 200, r.text
        assert len(executor_calls) >= 1, "run_in_executor was never called for config file read"
        exec_arg, fn_arg, _ = executor_calls[0]
        assert exec_arg is None
        assert callable(fn_arg)

    async def test_config_read_does_not_stall_concurrent_get(self, config_app, tmp_path):
        """While a slow config file read is in progress, a concurrent GET completes
        without being blocked by the file I/O.

        Strategy: patch Path.read_bytes to sleep inside the real thread-pool executor.
        Because the router now uses run_in_executor, the event loop stays free during
        that sleep and the concurrent /ping GET completes immediately.
        """
        import asyncio as _asyncio
        import json
        import time
        from pathlib import Path as _Path
        from unittest.mock import patch

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"server": {"port": 8000}}))

        # Add a trivial /ping-cfg route for the concurrent request.
        from fastapi.responses import JSONResponse as _JSONResponse

        @config_app.get("/ping-cfg")
        async def _ping_cfg():
            return _JSONResponse(content={"pong": True})

        read_started = _asyncio.Event()
        real_read_bytes = _Path.read_bytes

        # Capture the running loop in the async context; worker threads cannot
        # call asyncio.get_running_loop() — it raises RuntimeError there.
        running_loop = _asyncio.get_running_loop()

        def slow_read_bytes(self):
            """Slow read_bytes wrapper — signals start then sleeps."""
            running_loop.call_soon_threadsafe(read_started.set)
            time.sleep(0.15)
            return real_read_bytes(self)

        port = MagicMock()
        port.get_raw_config.side_effect = AttributeError("no get_raw_config")
        port.get_loaded_config_file.return_value = str(cfg_file)

        from httpx import ASGITransport, AsyncClient

        config_app.dependency_overrides[get_config_manager] = lambda: port

        with patch.object(_Path, "read_bytes", slow_read_bytes):
            async with AsyncClient(
                transport=ASGITransport(app=config_app), base_url="http://test"
            ) as ac:
                read_task = _asyncio.create_task(ac.get("/config/?source=file"))
                await _asyncio.wait_for(read_started.wait(), timeout=2.0)
                ping_r = await ac.get("/ping-cfg")
                read_r = await read_task

        assert ping_r.status_code == 200, "concurrent GET stalled during config file read"
        assert read_r.status_code == 200

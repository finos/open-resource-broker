"""Unit tests for the config router — /api/v1/config/*."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import get_config_manager
from orb.api.routers.config import router as config_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config_app():
    """Minimal FastAPI app with only the config router mounted."""
    app = FastAPI()
    app.include_router(config_router)
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

        with patch("orb.api.routers.config.get_config_manager", return_value=port):
            client = TestClient(config_app)
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

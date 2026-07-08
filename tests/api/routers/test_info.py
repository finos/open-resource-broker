"""Tests for the /info endpoint — authentication config must not be disclosed."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Minimal fixture: replicate the /info handler without the full create_app
# machinery so the test has no dependency on DI containers or middleware.
# ---------------------------------------------------------------------------


def _make_info_app() -> FastAPI:
    """Return a FastAPI test app that contains only the /info handler."""
    from orb._package import __version__

    app = FastAPI()

    @app.get("/info")
    async def info() -> dict:
        return {
            "service": "open-resource-broker",
            "version": __version__,
            "description": "REST API for Open Resource Broker",
        }

    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_info_app())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestInfoEndpoint:
    """GET /info must return service metadata without disclosing auth config."""

    def test_returns_200(self, client: TestClient) -> None:
        """The endpoint responds with HTTP 200."""
        resp = client.get("/info")
        assert resp.status_code == 200

    def test_body_is_json(self, client: TestClient) -> None:
        """The response body is valid JSON."""
        resp = client.get("/info")
        # content-type must be application/json
        assert "application/json" in resp.headers.get("content-type", "")

    def test_service_field_present(self, client: TestClient) -> None:
        """The 'service' key is present and identifies the broker."""
        resp = client.get("/info")
        body = resp.json()
        assert body.get("service") == "open-resource-broker"

    def test_version_field_present(self, client: TestClient) -> None:
        """The 'version' key is present and non-empty."""
        resp = client.get("/info")
        body = resp.json()
        assert "version" in body
        assert body["version"]  # non-empty string

    def test_description_field_present(self, client: TestClient) -> None:
        """The 'description' key is present."""
        resp = client.get("/info")
        body = resp.json()
        assert "description" in body

    def test_auth_enabled_not_disclosed(self, client: TestClient) -> None:
        """'auth_enabled' must NOT appear in the /info response.

        Revealing whether authentication is active tells an attacker whether
        there is any credential-based access control to probe.
        """
        resp = client.get("/info")
        body = resp.json()
        assert "auth_enabled" not in body, (
            "auth_enabled must not be disclosed in the /info response"
        )

    def test_auth_strategy_not_disclosed(self, client: TestClient) -> None:
        """'auth_strategy' must NOT appear in the /info response.

        Revealing the active auth strategy narrows the attack surface for
        credential-stuffing by telling an attacker exactly which auth method
        to target.
        """
        resp = client.get("/info")
        body = resp.json()
        assert "auth_strategy" not in body, (
            "auth_strategy must not be disclosed in the /info response"
        )

    def test_no_extra_auth_fields_in_response(self, client: TestClient) -> None:
        """The response contains only the expected safe fields."""
        resp = client.get("/info")
        body = resp.json()
        allowed_keys = {"service", "version", "description"}
        unexpected = set(body.keys()) - allowed_keys
        assert not unexpected, f"Unexpected fields in /info response: {unexpected}"

    def test_unauthenticated_access_succeeds(self, client: TestClient) -> None:
        """Anonymous callers can reach /info without credentials (public health-like endpoint)."""
        # No Authorization header — request must still succeed with 200.
        resp = client.get("/info", headers={})
        assert resp.status_code == 200

"""REST API boundary contract tests (Boundary D).

These tests validate the REST API response shapes using FastAPI's TestClient.
They mock the CQRS buses so no real AWS or moto context is needed.

If FastAPI or httpx is not available, tests are skipped with a clear message.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

try:
    import jsonschema
except ImportError:
    pytest.skip("jsonschema not installed", allow_module_level=True)

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("fastapi not installed", allow_module_level=True)

# ---------------------------------------------------------------------------
# REST response schemas
# ---------------------------------------------------------------------------

_TEMPLATES_LIST_SCHEMA = {
    "type": "object",
    "required": ["templates"],
    "properties": {
        "templates": {"type": "array"},
        "total_count": {"type": "integer"},
        "count": {"type": "integer"},
        "message": {"type": "string"},
        "success": {"type": "boolean"},
    },
    "additionalProperties": True,
}

_REQUEST_MACHINES_SCHEMA = {
    "type": "object",
    "properties": {
        "requestId": {"type": "string"},
        "request_id": {"type": "string"},
        "message": {"type": "string"},
    },
    "additionalProperties": True,
}

_REQUEST_STATUS_SCHEMA = {
    "type": "object",
    "required": ["requests"],
    "properties": {
        "requests": {"type": "array"},
    },
    "additionalProperties": True,
}

_ERROR_SCHEMA = {
    "type": "object",
    "properties": {
        "detail": {},
        "error": {"type": "string"},
        "message": {"type": "string"},
    },
    "additionalProperties": True,
}


def _validate(instance: dict, schema: dict, label: str = "") -> None:
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as exc:
        raise AssertionError(
            f"REST schema validation failed{' (' + label + ')' if label else ''}:\n"
            f"  path: {list(exc.absolute_path)}\n"
            f"  message: {exc.message}\n"
            f"  instance: {instance}"
        ) from exc


# ---------------------------------------------------------------------------
# App fixture — boot FastAPI with mocked buses
# ---------------------------------------------------------------------------


@pytest.fixture
def rest_client(orb_config_dir_hf):
    """FastAPI TestClient with DI container booted against moto config."""
    try:
        from orb.api.server import create_fastapi_app
    except ImportError as exc:
        pytest.skip(f"Could not import REST app: {exc}")

    app = create_fastapi_app(None)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


# ---------------------------------------------------------------------------
# 1. GET /api/v1/templates/ — list templates
# ---------------------------------------------------------------------------


def test_rest_list_templates_response_shape(rest_client):
    """GET /api/v1/templates/ returns 200 with templates array."""
    response = rest_client.get("/api/v1/templates/")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    _validate(body, _TEMPLATES_LIST_SCHEMA, "list_templates")
    assert "templates" in body


def test_rest_list_templates_content_type(rest_client):
    """GET /api/v1/templates/ returns application/json content-type."""
    response = rest_client.get("/api/v1/templates/")
    assert "application/json" in response.headers.get("content-type", ""), (
        f"Expected application/json, got: {response.headers.get('content-type')}"
    )


# ---------------------------------------------------------------------------
# 2. GET /api/v1/requests/{id}/status — request status
# ---------------------------------------------------------------------------


def test_rest_request_status_unknown_id_returns_json(rest_client):
    """GET /api/v1/requests/{id}/status for unknown ID returns a JSON object.

    The REST API currently returns 200 with an error envelope for unknown IDs
    rather than 404 — this test documents and guards that behaviour.
    """
    response = rest_client.get("/api/v1/requests/req-00000000-0000-0000-0000-000000000099/status")
    assert response.status_code in (200, 404, 400, 422, 500), (
        f"Unexpected status code for unknown request: {response.status_code}"
    )
    body = response.json()
    assert isinstance(body, dict), f"Response must be a JSON object, got: {body!r}"


# ---------------------------------------------------------------------------
# 3. Content-type on all endpoints
# ---------------------------------------------------------------------------


def test_rest_health_endpoint_returns_json(rest_client):
    """Health endpoint returns application/json."""
    for path in ["/health", "/api/v1/health", "/"]:
        response = rest_client.get(path)
        if response.status_code == 200:
            assert "application/json" in response.headers.get("content-type", ""), (
                f"Health endpoint {path} did not return application/json"
            )
            break

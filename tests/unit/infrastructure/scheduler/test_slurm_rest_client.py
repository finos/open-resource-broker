"""Unit tests for SlurmRestClient with HTTP mocked at the boundary.

Covers open-resource-broker-2706.4: no tests previously exercised the real
REST client implementation (only mocks at a higher layer).
"""

import pytest
import requests

from orb.infrastructure.scheduler.slurm.rest_client import (
    SlurmRestClient,
    SlurmRestClientError,
)


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_body=None, text: str = "") -> None:
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.text = text

    def json(self):
        return self._json_body


@pytest.fixture
def client() -> SlurmRestClient:
    return SlurmRestClient(base_url="https://slurmrestd.example.com", token="tok-123")


# ---------------------------------------------------------------------------
# Construction / validation
# ---------------------------------------------------------------------------


def test_rejects_non_http_scheme():
    with pytest.raises(ValueError, match="must start with http"):
        SlurmRestClient(base_url="ftp://slurmrestd.example.com")


def test_strips_trailing_slash_from_base_url():
    c = SlurmRestClient(base_url="https://slurmrestd.example.com/")
    assert c._url("nodes") == "https://slurmrestd.example.com/slurm/v0.0.44/nodes"


def test_headers_include_token_when_set(client):
    headers = client._get_headers()
    assert headers["X-SLURM-USER-TOKEN"] == "tok-123"


def test_headers_omit_token_when_not_set():
    c = SlurmRestClient(base_url="https://slurmrestd.example.com")
    headers = c._get_headers()
    assert "X-SLURM-USER-TOKEN" not in headers


def test_set_token_updates_headers(client):
    client.set_token("new-token")
    assert client._get_headers()["X-SLURM-USER-TOKEN"] == "new-token"


# ---------------------------------------------------------------------------
# get_nodes / get_node
# ---------------------------------------------------------------------------


def test_get_nodes_happy_path(client, monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: _FakeResponse(200, {"nodes": [{"name": "compute-001"}]})
    )

    assert client.get_nodes() == {"nodes": [{"name": "compute-001"}]}


def test_get_node_validates_name(client):
    with pytest.raises(ValueError, match="Invalid node_name"):
        client.get_node("compute-001; rm -rf /")


def test_get_node_happy_path(client, monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: _FakeResponse(200, {"nodes": [{"name": "compute-001"}]})
    )

    assert client.get_node("compute-001") == {"nodes": [{"name": "compute-001"}]}


# ---------------------------------------------------------------------------
# get_partitions / get_partition
# ---------------------------------------------------------------------------


def test_get_partitions_happy_path(client, monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: _FakeResponse(200, {"partitions": [{"name": "gpu"}]}),
    )

    assert client.get_partitions() == {"partitions": [{"name": "gpu"}]}


def test_get_partition_validates_name(client):
    with pytest.raises(ValueError, match="Invalid partition_name"):
        client.get_partition("gpu$(whoami)")


# ---------------------------------------------------------------------------
# Error paths: HTTP error status, connection failure, timeout, malformed body
# ---------------------------------------------------------------------------


def test_get_raises_rest_client_error_on_http_error_status(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(500, text="internal error"))

    with pytest.raises(SlurmRestClientError, match="HTTP 500"):
        client.get_nodes()


def test_get_returns_empty_on_connection_error(client, monkeypatch):
    def _raise(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", _raise)

    assert client.get_nodes() == {}


def test_get_returns_empty_on_timeout(client, monkeypatch):
    def _raise(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(requests, "get", _raise)

    assert client.get_nodes() == {}


def test_get_propagates_on_malformed_json_body(client, monkeypatch):
    class _BadJsonResponse(_FakeResponse):
        def json(self):
            raise ValueError("not JSON")

    monkeypatch.setattr(requests, "get", lambda *a, **k: _BadJsonResponse(200))

    with pytest.raises(ValueError, match="not JSON"):
        client.get_nodes()


# ---------------------------------------------------------------------------
# ping / is_available
# ---------------------------------------------------------------------------


def test_ping_true_on_2xx(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(200))

    assert client.ping() is True


def test_ping_false_on_4xx(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(404))

    assert client.ping() is False


def test_ping_false_on_connection_error(client, monkeypatch):
    def _raise(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", _raise)

    assert client.ping() is False


def test_ping_false_on_timeout(client, monkeypatch):
    def _raise(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(requests, "get", _raise)

    assert client.ping() is False


def test_is_available_true(client, monkeypatch):
    monkeypatch.setattr(client, "ping", lambda: True)

    assert client.is_available() is True


def test_is_available_false_on_unexpected_error(client, monkeypatch):
    def _raise():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(client, "ping", _raise)

    assert client.is_available() is False

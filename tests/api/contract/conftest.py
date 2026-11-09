"""Fixtures for the REST error contract suite: the real app, with auth off and on."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import CurrentUser, get_current_user
from orb.api.server import create_fastapi_app
from orb.config.schemas.server_schema import (
    AuthConfig,
    CORSConfig,
    RateLimitConfig,
    ServerConfig,
)
from orb.infrastructure.auth.strategy.bearer_token_strategy import BearerTokenStrategy
from tests.api.contract.harness import SECRET, clear


def _build_app(auth_enabled: bool) -> FastAPI:
    auth = (
        AuthConfig(  # type: ignore[call-arg]
            enabled=True,
            strategy="bearer_token",
            bearer_token={"secret_key": SECRET, "algorithm": "HS256", "token_expiry": 3600},
        )
        if auth_enabled
        else AuthConfig(enabled=False, strategy="replace")  # type: ignore[call-arg]
    )
    config = ServerConfig(  # type: ignore[call-arg]
        enabled=True,
        auth=auth,
        cors=CORSConfig(origins=["*"]),  # type: ignore[call-arg]
        rate_limiting=RateLimitConfig(enabled=False),  # type: ignore[call-arg]
    )
    app = create_fastapi_app(config)
    if not auth_enabled:
        # With authentication off every caller is an anonymous viewer; deployments that
        # need elevated access without authentication supply the identity themselves.
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="contract-admin", role="admin", claims={}
        )
    return app


def _bearer_headers() -> dict[str, str]:
    strategy = BearerTokenStrategy(secret_key=SECRET, algorithm="HS256", enabled=True)
    token = strategy._create_access_token(
        user_id="contract-admin", roles=["admin"], permissions=["*"]
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module", params=["auth_off", "auth_on"])
def auth_mode(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(scope="module")
def app(auth_mode: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FastAPI]:
    mp = pytest.MonkeyPatch()
    mp.setenv("ORB_CONFIG_DIR", str(tmp_path_factory.mktemp("orb-config")))
    try:
        yield _build_app(auth_mode == "auth_on")
    finally:
        mp.undo()


@pytest.fixture(scope="module")
def client(app: FastAPI, auth_mode: str):
    """Client that returns error responses instead of re-raising server errors."""
    headers = _bearer_headers() if auth_mode == "auth_on" else {}
    with TestClient(app, raise_server_exceptions=False, headers=headers) as test_client:
        yield test_client


@pytest.fixture
def clear_faults(app: FastAPI):
    yield
    clear(app)

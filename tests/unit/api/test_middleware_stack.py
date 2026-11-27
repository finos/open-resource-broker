"""``install_middleware`` and the modules split out of ``orb.api.server``."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI

from orb.api.loopback_admin import _load_loopback_token, _LoopbackAdminAuthWrapper
from orb.api.middleware_stack import _create_auth_strategy, install_middleware
from orb.config.schemas.server_schema import AuthConfig, ServerConfig

pytestmark = [pytest.mark.unit, pytest.mark.api]

_SECRET = "test-secret-key-for-install-middleware!!"


def _config(tmp_path, *, auth_enabled: bool, **overrides: Any) -> ServerConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "auth": AuthConfig(  # type: ignore[call-arg]
            enabled=auth_enabled,
            strategy="bearer_token",
            bearer_token={"secret_key": _SECRET, "algorithm": "HS256"},
        ),
        "trusted_hosts": ["orb.example.com"],
        "pid_file": str(tmp_path / "orb-server.pid"),
    }
    values.update(overrides)
    return ServerConfig(**values)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _isolate_loopback_state():
    saved = (
        set(_LoopbackAdminAuthWrapper._tokens),
        _LoopbackAdminAuthWrapper._token_file,
        _LoopbackAdminAuthWrapper._token_file_mtime,
    )
    _LoopbackAdminAuthWrapper._tokens = set()
    _LoopbackAdminAuthWrapper._token_file = None
    yield
    (
        _LoopbackAdminAuthWrapper._tokens,
        _LoopbackAdminAuthWrapper._token_file,
        _LoopbackAdminAuthWrapper._token_file_mtime,
    ) = saved


def test_returns_the_auth_port_the_auth_middleware_uses(tmp_path) -> None:
    app = FastAPI()

    auth_port = install_middleware(
        app, _config(tmp_path, auth_enabled=True), logging.getLogger("orb.api.server")
    )

    assert isinstance(auth_port, _LoopbackAdminAuthWrapper)
    auth_entry = next(m for m in app.user_middleware if m.cls.__name__ == "AuthMiddleware")
    assert auth_entry.kwargs["auth_port"] is auth_port


def test_returns_none_when_authentication_is_disabled(tmp_path) -> None:
    app = FastAPI()

    auth_port = install_middleware(
        app, _config(tmp_path, auth_enabled=False), logging.getLogger("orb.api.server")
    )

    assert auth_port is None
    assert "AuthMiddleware" not in [m.cls.__name__ for m in app.user_middleware]


def test_unknown_auth_strategy_refuses_to_build(tmp_path) -> None:
    from orb.domain.base.exceptions import ConfigurationError

    config = _config(tmp_path, auth_enabled=True)
    config.auth.strategy = "no-such-strategy"

    with pytest.raises(ConfigurationError, match="could not be created"):
        install_middleware(FastAPI(), config, logging.getLogger("orb.api.server"))


def test_split_modules_keep_logging_under_the_server_logger_name(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """Moved code must not change the logger name existing filters match on."""
    token_file = tmp_path / "orb-server.token"
    token_file.write_text("loopback-secret")
    config = _config(tmp_path, auth_enabled=True, trusted_hosts=[])

    with caplog.at_level(logging.DEBUG, logger="orb.api.server"):
        install_middleware(FastAPI(), config, logging.getLogger("orb.api.server"))
        _load_loopback_token(config)
        _LoopbackAdminAuthWrapper.rotate_token()
        _create_auth_strategy(SimpleNamespace(strategy="no-such-strategy"))
        _LoopbackAdminAuthWrapper(object())

    by_module = {
        "orb.api.middleware_stack": {r for r in caplog.records if r.module == "middleware_stack"},
        "orb.api.loopback_admin": {r for r in caplog.records if r.module == "loopback_admin"},
    }
    assert by_module["orb.api.middleware_stack"], "no record came from middleware_stack"
    assert by_module["orb.api.loopback_admin"], "no record came from loopback_admin"
    for records in by_module.values():
        assert {r.name for r in records} == {"orb.api.server"}

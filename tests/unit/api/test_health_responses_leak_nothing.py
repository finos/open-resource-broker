"""Health responses served by /health and the CLI/MCP health command carry no secrets.

Real AWS, storage and system checks run against a fake client and failing
probes that return an account ID, ARN, user ID, paths and exception text;
none of it may reach a response body, the formatted CLI checks, or the console.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from orb.api.dependencies import get_health_check_port
from orb.api.server import create_fastapi_app
from orb.application.ports.scheduler_port import SchedulerPort
from orb.config.schemas.server_schema import AuthConfig, ServerConfig
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.console_port import ConsolePort
from orb.domain.base.ports.health_check_port import HealthCheckPort
from orb.monitoring.health import (
    HealthCheck,
    HealthCheckConfig,
    register_storage_health_checks,
)
from orb.providers.aws.health import register_aws_health_checks

_ACCOUNT = "123456789012"
_ARN = "arn:aws:iam::123456789012:user/ops"
_USER_ID = "AIDAEXAMPLEUSERID"
_RAW_ERROR = "Unable to locate credentials for /home/ops/.aws/credentials"
_PATH = "/srv/orb/data/templates.json"
_TABLE = "orb-prod-requests-table"
_SECRETS = (_ACCOUNT, _ARN, _USER_ID, _RAW_ERROR, "/home/ops", _PATH, _TABLE, "eu-west-1")


def _aws_client(*, fail: bool) -> MagicMock:
    client = MagicMock()
    if fail:
        client.sts_client.get_caller_identity.side_effect = RuntimeError(_RAW_ERROR)
        client.ec2_client.describe_instances.side_effect = RuntimeError(_RAW_ERROR)
    else:
        client.sts_client.get_caller_identity.return_value = {
            "Account": _ACCOUNT,
            "Arn": _ARN,
            "UserId": _USER_ID,
        }
        client.ec2_client.describe_instances.return_value = {"Reservations": []}
    return client


class _LeakyStorage:
    """A storage port whose health probe reports paths, table names and errors."""

    def __init__(self, *, raises: bool) -> None:
        self._raises = raises

    def is_healthy(self) -> tuple[bool, dict[str, Any]]:
        if self._raises:
            raise RuntimeError(f"{_RAW_ERROR} {_PATH}")
        return False, {
            "type": "dynamodb",
            "reason": "health probe failed",
            "error": f"{_RAW_ERROR} table {_TABLE} in eu-west-1",
        }


def _health_check(tmp_path: Path, *, aws_fails: bool, storage_raises: bool) -> HealthCheck:
    health = HealthCheck(config=HealthCheckConfig(health_dir=tmp_path / "health"))
    client = _aws_client(fail=aws_fails)
    register_aws_health_checks(health, lambda: client)
    register_storage_health_checks(health, _LeakyStorage(raises=storage_raises))
    return health


def _assert_clean(text: str) -> None:
    for secret in _SECRETS:
        assert secret not in text, f"{secret!r} leaked"


@pytest.fixture(params=[False, True], ids=["aws-ok", "aws-failing"])
def aws_fails(request: pytest.FixtureRequest) -> bool:
    return request.param


@pytest.fixture(params=[False, True], ids=["storage-reports", "storage-raises"])
def storage_raises(request: pytest.FixtureRequest) -> bool:
    return request.param


@pytest.mark.unit
@pytest.mark.api
def test_get_health_endpoint_leaks_no_identity_paths_or_exception_text(
    tmp_path: Path, aws_fails: bool, storage_raises: bool
) -> None:
    health = _health_check(tmp_path, aws_fails=aws_fails, storage_raises=storage_raises)
    server_config = ServerConfig(  # type: ignore[call-arg]
        enabled=True,
        auth=AuthConfig(enabled=False, strategy="replace"),  # type: ignore[call-arg]
    )
    with patch(
        "orb.api.server._register_routers", side_effect=lambda app: app.include_router(APIRouter())
    ):
        app = create_fastapi_app(server_config)
    app.dependency_overrides[get_health_check_port] = lambda: health

    response = TestClient(app).get("/health")

    body = response.text
    _assert_clean(body)
    checks = json.loads(body)["checks"]
    assert {"aws", "ec2", "database"} <= set(checks)
    if aws_fails:
        assert checks["aws"]["details"]["error"]
    else:
        assert checks["aws"]["details"] == {"api_status": "available"}


@pytest.mark.unit
@pytest.mark.api
def test_readyz_endpoint_leaks_nothing(tmp_path: Path, aws_fails: bool) -> None:
    health = _health_check(tmp_path, aws_fails=aws_fails, storage_raises=False)
    server_config = ServerConfig(  # type: ignore[call-arg]
        enabled=True,
        auth=AuthConfig(enabled=False, strategy="replace"),  # type: ignore[call-arg]
    )
    with patch(
        "orb.api.server._register_routers", side_effect=lambda app: app.include_router(APIRouter())
    ):
        app = create_fastapi_app(server_config)
    app.dependency_overrides[get_health_check_port] = lambda: health

    _assert_clean(TestClient(app).get("/readyz").text)


@pytest.mark.unit
def test_cli_health_command_leaks_nothing(
    tmp_path: Path, aws_fails: bool, storage_raises: bool
) -> None:
    from orb.interface.health_command_handler import handle_health_check

    health = _health_check(tmp_path, aws_fails=aws_fails, storage_raises=storage_raises)
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.get_template_paths.return_value = []
    scheduler.get_working_directory.return_value = str(tmp_path)
    scheduler.get_logs_directory.return_value = str(tmp_path)
    scheduler.should_log_to_console.return_value = True
    scheduler.format_health_response.return_value = {"summary": {"passed": 0, "total": 0}}
    config = MagicMock(spec=ConfigurationPort)
    config.get_config_file_path.return_value = None
    console = MagicMock(spec=ConsolePort)
    services: dict[Any, Any] = {
        SchedulerPort: scheduler,
        ConfigurationPort: config,
        HealthCheckPort: health,
        ConsolePort: console,
    }
    container = MagicMock()
    container.get.side_effect = lambda t: services.get(t, MagicMock())
    args = MagicMock()
    args._container = container

    result = handle_health_check(args)

    checks = scheduler.format_health_response.call_args[0][0]
    _assert_clean(repr(checks))
    _assert_clean(repr(result))
    _assert_clean(repr(console.mock_calls))
    assert any(c["name"] == "aws" for c in checks)


_HS256_SECRET = "health-detail-test-secret-key-0123456789!"
_SUMMARY_KEYS = {"service", "version", "status"}


@pytest.fixture
def _no_loopback_tokens():
    """Isolate the class-level loopback token state of the auth wrapper."""
    from orb.api.server import _LoopbackAdminAuthWrapper

    saved = (
        set(_LoopbackAdminAuthWrapper._tokens),
        _LoopbackAdminAuthWrapper._token_file,
        _LoopbackAdminAuthWrapper._token_file_mtime,
    )
    _LoopbackAdminAuthWrapper._tokens = set()
    _LoopbackAdminAuthWrapper._token_file = None
    _LoopbackAdminAuthWrapper._token_file_mtime = 0.0
    yield _LoopbackAdminAuthWrapper
    (
        _LoopbackAdminAuthWrapper._tokens,
        _LoopbackAdminAuthWrapper._token_file,
        _LoopbackAdminAuthWrapper._token_file_mtime,
    ) = saved


def _authenticated_app(tmp_path: Path, *, storage_raises: bool = False) -> Any:
    health = _health_check(tmp_path, aws_fails=False, storage_raises=storage_raises)
    server_config = ServerConfig(  # type: ignore[call-arg]
        enabled=True,
        auth=AuthConfig(  # type: ignore[call-arg]
            enabled=True,
            strategy="bearer_token",
            bearer_token={"secret_key": _HS256_SECRET, "algorithm": "HS256"},
        ),
        # The test client's peer is "testclient"; trusting it as a proxy makes
        # X-Forwarded-For resolve the client IP
        trusted_proxies=["testclient"],
    )
    with patch(
        "orb.api.server._register_routers", side_effect=lambda app: app.include_router(APIRouter())
    ):
        app = create_fastapi_app(server_config)
    app.dependency_overrides[get_health_check_port] = lambda: health
    return app


def _valid_token() -> str:
    from orb.infrastructure.auth.strategy.bearer_token_strategy import BearerTokenStrategy

    strategy = BearerTokenStrategy(secret_key=_HS256_SECRET, algorithm="HS256", enabled=True)
    return strategy._create_access_token(user_id="ops", roles=["user"], permissions=["read"])


@pytest.mark.unit
@pytest.mark.api
@pytest.mark.usefixtures("_no_loopback_tokens")
class TestHealthDetailNeedsCredentials:
    """With auth enabled, /health shows per-check detail only to authenticated callers."""

    def test_anonymous_caller_gets_only_the_overall_status(self, tmp_path: Path) -> None:
        client = TestClient(_authenticated_app(tmp_path))

        anonymous = client.get("/health")
        authenticated = client.get("/health", headers={"Authorization": f"Bearer {_valid_token()}"})

        assert set(anonymous.json()) == _SUMMARY_KEYS
        assert anonymous.json()["status"] == authenticated.json()["status"]
        assert anonymous.status_code == authenticated.status_code

    def test_valid_bearer_gets_the_checks(self, tmp_path: Path) -> None:
        client = TestClient(_authenticated_app(tmp_path))

        response = client.get("/health", headers={"Authorization": f"Bearer {_valid_token()}"})

        assert {"system", "disk"} <= set(response.json()["checks"])

    def test_invalid_bearer_gets_the_summary_not_a_401(self, tmp_path: Path) -> None:
        client = TestClient(_authenticated_app(tmp_path))

        response = client.get("/health", headers={"Authorization": "Bearer not-a-real-token"})

        assert response.status_code in (200, 503)
        assert set(response.json()) == _SUMMARY_KEYS

    def test_status_code_is_kept_for_anonymous_callers(self, tmp_path: Path) -> None:
        client = TestClient(_authenticated_app(tmp_path, storage_raises=True))

        anonymous = client.get("/health")
        authenticated = client.get("/health", headers={"Authorization": f"Bearer {_valid_token()}"})

        assert anonymous.status_code == authenticated.status_code
        assert set(anonymous.json()) == _SUMMARY_KEYS

    def test_loopback_token_from_a_remote_peer_with_forwarded_loopback_gets_the_summary(
        self, tmp_path: Path, _no_loopback_tokens: Any
    ) -> None:
        _no_loopback_tokens._tokens = {"loopback-secret"}
        client = TestClient(_authenticated_app(tmp_path))

        response = client.get(
            "/health",
            headers={
                "Authorization": "Bearer loopback-secret",
                "X-Forwarded-For": "127.0.0.1",
            },
        )

        assert set(response.json()) == _SUMMARY_KEYS

    def test_loopback_token_from_a_loopback_peer_gets_the_checks(
        self, tmp_path: Path, _no_loopback_tokens: Any
    ) -> None:
        _no_loopback_tokens._tokens = {"loopback-secret"}
        client = TestClient(_authenticated_app(tmp_path), client=("127.0.0.1", 50000))

        response = client.get("/health", headers={"Authorization": "Bearer loopback-secret"})

        assert "checks" in response.json()

"""Unit tests for IAMAuthStrategy."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.infrastructure.adapters.ports.auth import AuthContext, AuthStatus


def _make_context(**kwargs) -> AuthContext:
    defaults: dict[str, Any] = {
        "method": "GET",
        "path": "/api/v1/machines",
        "headers": {},
        "query_params": {},
    }
    defaults.update(kwargs)
    return AuthContext(**defaults)


def _make_logger() -> MagicMock:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_strategy(assume_permissions: bool = True) -> Any:
    """Build IAMAuthStrategy with mocked AWS clients."""
    from orb.providers.aws.auth.iam_strategy import IAMAuthStrategy

    logger = _make_logger()

    with patch("orb.providers.aws.auth.iam_strategy.AWSSessionFactory") as mock_factory:
        mock_session = MagicMock()
        mock_sts = MagicMock()
        mock_iam = MagicMock()
        mock_session.client.side_effect = lambda svc, **kw: mock_sts if svc == "sts" else mock_iam
        mock_factory.create_session.return_value = mock_session

        strategy = IAMAuthStrategy(
            logger=logger,
            region="us-east-1",
            assume_permissions=assume_permissions,
        )

    strategy.sts_client = mock_sts
    strategy.iam_client = mock_iam
    return strategy


# ---------------------------------------------------------------------------
# authenticate() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_authenticate_success_assume_permissions(monkeypatch):
    """IAM authenticate returns SUCCESS with permissions when assume_permissions=True and dev env var is set."""
    monkeypatch.setenv("ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY", "true")

    strategy = _make_strategy(assume_permissions=True)
    strategy.sts_client.get_caller_identity.return_value = {
        "UserId": "AIDAEXAMPLE",
        "Account": "123456789012",
        "Arn": "arn:aws:iam::123456789012:user/testuser",
    }

    result = await strategy.authenticate(_make_context())

    assert result.status == AuthStatus.SUCCESS
    assert result.user_id is not None
    assert len(result.permissions) > 0


# ---------------------------------------------------------------------------
# authenticate() — disabled strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_authenticate_disabled():
    """IAM authenticate returns FAILED when strategy is disabled."""
    strategy = _make_strategy()
    strategy.enabled = False

    result = await strategy.authenticate(_make_context())

    assert result.status == AuthStatus.FAILED
    assert "disabled" in (result.error_message or "").lower()


# ---------------------------------------------------------------------------
# authenticate() — no credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_authenticate_no_credentials():
    """IAM authenticate returns FAILED when AWS credentials are absent."""
    from botocore.exceptions import NoCredentialsError

    strategy = _make_strategy()
    strategy.sts_client.get_caller_identity.side_effect = NoCredentialsError()

    result = await strategy.authenticate(_make_context())

    assert result.status == AuthStatus.FAILED
    assert "credentials" in (result.error_message or "").lower()


# ---------------------------------------------------------------------------
# _check_permissions — assume_permissions=False → deny
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_deny_without_assume():
    """_check_permissions returns empty list when assume_permissions is False."""
    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    assert permissions == []


# ---------------------------------------------------------------------------
# _determine_roles — admin detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_determine_roles_admin():
    """_determine_roles detects admin when ARN contains a known admin pattern."""
    strategy = _make_strategy()

    roles = await strategy._determine_roles(
        {"Arn": "arn:aws:iam::123456789012:user/Admin"},
        ["ec2:DescribeInstances"],
    )

    assert "admin" in roles


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_determine_roles_service_account():
    """_determine_roles adds service_account role for role-based ARNs."""
    strategy = _make_strategy()

    roles = await strategy._determine_roles(
        {"Arn": "arn:aws:iam::123:role/my-service-role"},
        [],
    )

    assert "service_account" in roles


# ---------------------------------------------------------------------------
# from_auth_config classmethod
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_iam_from_auth_config_defaults():
    """from_auth_config builds strategy using IAMAuthSubConfig defaults."""
    from orb.config.schemas.server_schema import AuthConfig

    auth_config = AuthConfig(strategy="iam")

    with patch("orb.providers.aws.auth.iam_strategy.AWSSessionFactory") as mock_factory:
        mock_session = MagicMock()
        mock_session.client.return_value = MagicMock()
        mock_factory.create_session.return_value = mock_session

        from orb.providers.aws.auth.iam_strategy import IAMAuthStrategy

        strategy = IAMAuthStrategy.from_auth_config(auth_config)

    assert strategy.region == "us-east-1"
    assert strategy._assume_permissions is False


# ---------------------------------------------------------------------------
# assume_permissions=True without env var → silently disabled + CRITICAL logged
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_iam_assume_permissions_without_env_var_is_disabled(monkeypatch):
    """
    assume_permissions=True in config is ignored when
    ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY is not set.
    A CRITICAL warning must be logged and _assume_permissions must be False.
    """
    monkeypatch.delenv("ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY", raising=False)

    from orb.providers.aws.auth.iam_strategy import IAMAuthStrategy

    logger = _make_logger()

    with patch("orb.providers.aws.auth.iam_strategy.AWSSessionFactory") as mock_factory:
        mock_session = MagicMock()
        mock_session.client.return_value = MagicMock()
        mock_factory.create_session.return_value = mock_session

        strategy = IAMAuthStrategy(
            logger=logger,
            region="us-east-1",
            assume_permissions=True,  # config says True …
        )

    # … but env var absent → must be disabled
    assert strategy._assume_permissions is False

    # CRITICAL must have been logged to warn the operator
    critical_calls = logger.critical.call_args_list
    assert critical_calls, "Expected at least one logger.critical() call"
    combined = " ".join(str(c) for c in critical_calls)
    assert "ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY" in combined


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_assume_permissions_without_env_var_returns_empty_permissions(monkeypatch):
    """
    When assume_permissions=True but env var is absent, _check_permissions
    returns an empty list (deny-all).
    """
    monkeypatch.delenv("ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY", raising=False)

    from orb.providers.aws.auth.iam_strategy import IAMAuthStrategy

    logger = _make_logger()

    with patch("orb.providers.aws.auth.iam_strategy.AWSSessionFactory") as mock_factory:
        mock_session = MagicMock()
        mock_session.client.return_value = MagicMock()
        mock_factory.create_session.return_value = mock_session

        strategy = IAMAuthStrategy(
            logger=logger,
            region="us-east-1",
            assume_permissions=True,
        )

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})
    assert permissions == []


@pytest.mark.unit
def test_iam_assume_permissions_with_env_var_is_active(monkeypatch):
    """
    assume_permissions=True is honoured when
    ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY=true is set.
    """
    monkeypatch.setenv("ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY", "true")

    from orb.providers.aws.auth.iam_strategy import IAMAuthStrategy

    logger = _make_logger()

    with patch("orb.providers.aws.auth.iam_strategy.AWSSessionFactory") as mock_factory:
        mock_session = MagicMock()
        mock_session.client.return_value = MagicMock()
        mock_factory.create_session.return_value = mock_session

        strategy = IAMAuthStrategy(
            logger=logger,
            region="us-east-1",
            assume_permissions=True,
        )

    assert strategy._assume_permissions is True

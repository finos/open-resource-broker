"""Unit tests for IAMAuthStrategy."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

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


def _make_paginator_mock(pages: list[list[tuple[str, str]]]) -> MagicMock:
    """Build a mock paginator that yields the given pages of (action, decision) tuples.

    Args:
        pages: Each item is a list of (action_name, eval_decision) tuples for one page.

    Returns:
        MagicMock configured so ``paginator.paginate(...)`` iterates over the pages.
    """
    paginator = MagicMock()
    built_pages = [
        {
            "EvaluationResults": [
                {"EvalActionName": action, "EvalDecision": decision}
                for action, decision in page_entries
            ]
        }
        for page_entries in pages
    ]
    paginator.paginate.return_value = iter(built_pages)
    return paginator


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
# _check_permissions — real SimulatePrincipalPolicy evaluation (via paginator)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_allowed_action_is_granted():
    """_check_permissions returns the action when SimulatePrincipalPolicy says allowed."""
    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False
    strategy.required_actions = ["ec2:DescribeInstances"]

    paginator = _make_paginator_mock([[("ec2:DescribeInstances", "allowed")]])
    strategy.iam_client.get_paginator.return_value = paginator

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    assert "ec2:DescribeInstances" in permissions
    strategy.iam_client.get_paginator.assert_called_once_with("simulate_principal_policy")
    paginator.paginate.assert_called_once_with(
        PolicySourceArn="arn:aws:iam::123:user/u",
        ActionNames=["ec2:DescribeInstances"],
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_explicit_deny_not_granted():
    """_check_permissions excludes actions with explicitDeny decision."""
    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False
    strategy.required_actions = ["ec2:TerminateInstances"]

    paginator = _make_paginator_mock([[("ec2:TerminateInstances", "explicitDeny")]])
    strategy.iam_client.get_paginator.return_value = paginator

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    assert permissions == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_implicit_deny_not_granted():
    """_check_permissions excludes actions with implicitDeny decision."""
    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False
    strategy.required_actions = ["ec2:RunInstances"]

    paginator = _make_paginator_mock([[("ec2:RunInstances", "implicitDeny")]])
    strategy.iam_client.get_paginator.return_value = paginator

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    assert permissions == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_mixed_decisions():
    """_check_permissions returns only the allowed subset from a mixed response."""
    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False
    strategy.required_actions = [
        "ec2:DescribeInstances",
        "ec2:RunInstances",
        "ec2:TerminateInstances",
    ]

    paginator = _make_paginator_mock(
        [
            [
                ("ec2:DescribeInstances", "allowed"),
                ("ec2:RunInstances", "implicitDeny"),
                ("ec2:TerminateInstances", "explicitDeny"),
            ]
        ]
    )
    strategy.iam_client.get_paginator.return_value = paginator

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    assert permissions == ["ec2:DescribeInstances"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_api_error_denies_all():
    """_check_permissions returns [] (fail secure) when SimulatePrincipalPolicy raises ClientError."""
    from botocore.exceptions import ClientError

    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False
    strategy.required_actions = ["ec2:DescribeInstances"]

    error_response = {"Error": {"Code": "AccessDenied", "Message": "Not authorized"}}
    paginator = MagicMock()
    paginator.paginate.side_effect = ClientError(error_response, "SimulatePrincipalPolicy")
    strategy.iam_client.get_paginator.return_value = paginator

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    assert permissions == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_unexpected_error_denies_all():
    """_check_permissions returns [] (fail secure) on any unexpected exception."""
    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False
    strategy.required_actions = ["ec2:DescribeInstances"]

    paginator = MagicMock()
    paginator.paginate.side_effect = RuntimeError("network timeout")
    strategy.iam_client.get_paginator.return_value = paginator

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    assert permissions == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_empty_arn_denies_all():
    """_check_permissions returns [] when caller ARN is missing from identity."""
    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False

    permissions = await strategy._check_permissions({})

    assert permissions == []
    strategy.iam_client.get_paginator.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_assume_permissions_dev_flag_bypasses_simulation(monkeypatch):
    """assume_permissions dev-flag returns all required_actions without calling simulate."""
    monkeypatch.setenv("ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY", "true")

    strategy = _make_strategy(assume_permissions=True)
    assert strategy._assume_permissions is True

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    strategy.iam_client.get_paginator.assert_not_called()
    assert "ec2:DescribeInstances" in permissions
    assert "hostfactory:list_templates" in permissions


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

    auth_config = AuthConfig(strategy="iam")  # type: ignore[call-arg]  # pydantic default fields

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


# ---------------------------------------------------------------------------
# Regression: ThrottlingException → deny-all (H1/H2 error path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_throttling_exception_denies_all():
    """ThrottlingException from SimulatePrincipalPolicy must deny all permissions (fail secure)."""
    from botocore.exceptions import ClientError

    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False
    strategy.required_actions = ["ec2:DescribeInstances"]

    error_response = {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}
    paginator = MagicMock()
    paginator.paginate.side_effect = ClientError(error_response, "SimulatePrincipalPolicy")
    strategy.iam_client.get_paginator.return_value = paginator

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    assert permissions == [], "ThrottlingException must deny all — never grant on error"


# ---------------------------------------------------------------------------
# Regression: >100 actions paginated across two pages (H2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_paginated_results_fully_consumed():
    """All EvaluationResults pages are consumed — page-2 allowed actions are included."""
    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False

    # Simulate 101 required actions split across two pages: 100 on page 1, 1 on page 2.
    page1_actions = [f"ec2:Action{i}" for i in range(100)]
    page2_actions = ["ec2:ActionPage2"]
    strategy.required_actions = page1_actions + page2_actions

    # Page 1: all denied except Action0 and Action99.
    page1_entries: list[tuple[str, str]] = [
        (f"ec2:Action{i}", "allowed" if i in (0, 99) else "implicitDeny") for i in range(100)
    ]
    # Page 2: the sole action is allowed.
    page2_entries: list[tuple[str, str]] = [("ec2:ActionPage2", "allowed")]

    paginator = _make_paginator_mock([page1_entries, page2_entries])
    strategy.iam_client.get_paginator.return_value = paginator

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    assert "ec2:Action0" in permissions
    assert "ec2:Action99" in permissions
    assert "ec2:ActionPage2" in permissions, "Page-2 allowed result must be included"
    # Denied actions must not appear.
    assert "ec2:Action1" not in permissions
    assert "ec2:Action50" not in permissions
    # Paginator must have been iterated (paginate called once, yielding 2 pages).
    paginator.paginate.assert_called_once()


# ---------------------------------------------------------------------------
# Regression: boto3 calls go through asyncio.to_thread (H1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_get_caller_identity_uses_to_thread(monkeypatch):
    """_get_caller_identity must delegate the sync boto3 call via asyncio.to_thread."""
    import asyncio as _asyncio

    strategy = _make_strategy(assume_permissions=False)
    strategy.sts_client.get_caller_identity.return_value = {
        "UserId": "AIDATEST",
        "Account": "111122223333",
        "Arn": "arn:aws:iam::111122223333:user/tester",
    }

    to_thread_calls: list[Any] = []
    original_to_thread = _asyncio.to_thread

    async def _spy_to_thread(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        to_thread_calls.append(func)
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(_asyncio, "to_thread", _spy_to_thread)

    identity = await strategy._get_caller_identity()

    assert identity is not None
    assert any(
        getattr(fn, "__self__", None) is strategy.sts_client
        or fn is strategy.sts_client.get_caller_identity
        for fn in to_thread_calls
    ), "get_caller_identity should have been called via asyncio.to_thread"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_uses_to_thread(monkeypatch):
    """_check_permissions must delegate the paginator iteration via asyncio.to_thread."""
    import asyncio as _asyncio

    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False
    strategy.required_actions = ["ec2:DescribeInstances"]

    paginator = _make_paginator_mock([[("ec2:DescribeInstances", "allowed")]])
    strategy.iam_client.get_paginator.return_value = paginator

    to_thread_called = False
    original_to_thread = _asyncio.to_thread

    async def _spy_to_thread(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal to_thread_called
        to_thread_called = True
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(_asyncio, "to_thread", _spy_to_thread)

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    assert "ec2:DescribeInstances" in permissions
    assert to_thread_called, "_check_permissions must call asyncio.to_thread for the paginator"


# ---------------------------------------------------------------------------
# Regression: required_actions=[] is honoured as empty (LOW)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_iam_required_actions_empty_list_is_honoured(monkeypatch):
    """required_actions=[] must be kept as-is — not replaced by the EC2 defaults."""
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
            required_actions=[],
        )

    assert strategy.required_actions == [], (
        "required_actions=[] must be honoured as empty, not silently replaced by EC2 defaults"
    )


@pytest.mark.unit
def test_iam_required_actions_none_applies_defaults(monkeypatch):
    """required_actions=None (not provided) must apply the hardcoded EC2 defaults."""
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
            required_actions=None,
        )

    assert "ec2:DescribeInstances" in strategy.required_actions
    assert "ec2:RunInstances" in strategy.required_actions
    assert "ec2:TerminateInstances" in strategy.required_actions


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iam_check_permissions_empty_required_actions_returns_empty_granted():
    """When required_actions=[], _check_permissions returns [] (nothing to grant)."""
    strategy = _make_strategy(assume_permissions=False)
    strategy._assume_permissions = False
    strategy.required_actions = []

    # Paginator returns no results since there are no actions to check.
    paginator = _make_paginator_mock([[]])
    strategy.iam_client.get_paginator.return_value = paginator

    permissions = await strategy._check_permissions({"Arn": "arn:aws:iam::123:user/u"})

    assert permissions == []

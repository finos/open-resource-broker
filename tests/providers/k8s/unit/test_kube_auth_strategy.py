"""Unit tests for KubeAuthStrategy — Kubernetes inbound HTTP auth via TokenReview.

These tests mock the Kubernetes SDK's ``AuthenticationV1Api.create_token_review``
call so they run without a real cluster.

Coverage:
- Token accepted by the API server → AuthResult SUCCESS with mapped role
- Token rejected (not authenticated) → AuthResult INVALID
- TokenReview API error (exception) → AuthResult FAILED
- Bearer header missing → AuthResult FAILED
- strategy disabled → AuthResult FAILED
- SA principal extraction from "system:serviceaccount:<ns>:<name>"
- Role mapping: exact match, wildcard-namespace match, default
- is_enabled / get_strategy_name
- from_auth_config factory (attribute extraction)
- refresh_token and revoke_token stubs
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.adapters.ports.auth import AuthContext, AuthResult, AuthStatus
from orb.providers.k8s.auth.kube_auth_strategy import KubeAuthStrategy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_logger() -> MagicMock:
    """Return a mock LoggingPort."""
    logger = MagicMock()
    for method in ("debug", "info", "warning", "error", "critical"):
        setattr(logger, method, MagicMock())
    return logger


def _make_strategy(
    *,
    sa_role_mapping: dict | None = None,
    audiences: list | None = None,
    enabled: bool = True,
    kubernetes_client: Any = None,
) -> KubeAuthStrategy:
    """Construct a KubeAuthStrategy with a mock logger."""
    return KubeAuthStrategy(
        logger=_make_logger(),
        kubernetes_client=kubernetes_client,
        sa_role_mapping=sa_role_mapping or {},
        audiences=audiences,
        enabled=enabled,
    )


def _make_token_review_response(
    *, authenticated: bool, username: str = "", uid: str = "", error: str = ""
) -> Any:
    """Build a fake V1TokenReview response object."""
    user_info = SimpleNamespace(username=username, uid=uid) if username else None
    status = SimpleNamespace(
        authenticated=authenticated,
        user=user_info,
        error=error if error else None,
    )
    return SimpleNamespace(status=status)


def _bearer_context(token: str = "sa-jwt-token") -> AuthContext:
    return AuthContext(
        method="GET",
        path="/api/v1/requests",
        headers={"authorization": f"Bearer {token}"},
        query_params={},
    )


# ---------------------------------------------------------------------------
# Strategy identity
# ---------------------------------------------------------------------------


def test_get_strategy_name() -> None:
    strategy = _make_strategy()
    assert strategy.get_strategy_name() == "kubernetes"


def test_is_enabled_true() -> None:
    strategy = _make_strategy(enabled=True)
    assert strategy.is_enabled() is True


def test_is_enabled_false() -> None:
    strategy = _make_strategy(enabled=False)
    assert strategy.is_enabled() is False


# ---------------------------------------------------------------------------
# authenticate — header extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_missing_bearer_header_returns_failed() -> None:
    strategy = _make_strategy()
    ctx = AuthContext(method="GET", path="/api/v1", headers={}, query_params={})
    result = await strategy.authenticate(ctx)
    assert result.status == AuthStatus.FAILED
    assert (
        "Authorization" in result.error_message
        or "authorization" in (result.error_message or "").lower()
    )


@pytest.mark.asyncio
async def test_authenticate_non_bearer_scheme_returns_failed() -> None:
    strategy = _make_strategy()
    ctx = AuthContext(
        method="GET",
        path="/api/v1",
        headers={"authorization": "Basic dXNlcjpwYXNz"},
        query_params={},
    )
    result = await strategy.authenticate(ctx)
    assert result.status == AuthStatus.FAILED


@pytest.mark.asyncio
async def test_authenticate_disabled_returns_failed() -> None:
    strategy = _make_strategy(enabled=False)
    result = await strategy.authenticate(_bearer_context())
    assert result.status == AuthStatus.FAILED
    assert "disabled" in (result.error_message or "").lower()


# ---------------------------------------------------------------------------
# validate_token — TokenReview success paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_token_success_maps_to_default_user_role() -> None:
    """Token authenticated by API server with no role mapping → default user role."""
    strategy = _make_strategy(sa_role_mapping={})

    with patch.object(
        strategy,
        "_do_token_review",
        return_value=AuthResult(
            status=AuthStatus.SUCCESS,
            user_id="abc-123",
            user_roles=["user"],
            permissions=["hostfactory:list_templates", "hostfactory:get_status"],
            metadata={
                "strategy": "kubernetes",
                "username": "system:serviceaccount:orb-system:orb-worker",
                "principal": "orb-system:orb-worker",
            },
        ),
    ):
        result = await strategy.validate_token("valid-token")

    assert result.status == AuthStatus.SUCCESS
    assert "user" in result.user_roles
    assert result.metadata.get("strategy") == "kubernetes"


@pytest.mark.asyncio
async def test_validate_token_success_with_mapped_admin_role() -> None:
    """SA principal in role mapping → mapped role returned."""
    mapping = {"orb-system:orb-admin": ["admin"]}
    strategy = _make_strategy(sa_role_mapping=mapping)

    expected_roles = ["admin"]
    expected_perms = {
        "hostfactory:*",
        "system:*",
        "hostfactory:list_templates",
        "hostfactory:get_status",
    }

    with patch.object(
        strategy,
        "_do_token_review",
        return_value=AuthResult(
            status=AuthStatus.SUCCESS,
            user_id="uid-99",
            user_roles=expected_roles,
            permissions=list(expected_perms),
            metadata={
                "strategy": "kubernetes",
                "username": "system:serviceaccount:orb-system:orb-admin",
                "principal": "orb-system:orb-admin",
            },
        ),
    ):
        result = await strategy.validate_token("admin-sa-token")

    assert result.status == AuthStatus.SUCCESS
    assert "admin" in result.user_roles


# ---------------------------------------------------------------------------
# validate_token — TokenReview rejection + error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_token_rejected_by_api_server_returns_invalid() -> None:
    """API server returns authenticated=False → INVALID."""
    strategy = _make_strategy()

    with patch.object(
        strategy,
        "_do_token_review",
        return_value=AuthResult(
            status=AuthStatus.INVALID,
            error_message="Token rejected by Kubernetes API server: token has expired",
        ),
    ):
        result = await strategy.validate_token("expired-token")

    assert result.status == AuthStatus.INVALID
    assert result.error_message is not None


@pytest.mark.asyncio
async def test_validate_token_api_error_returns_failed() -> None:
    """Exception from TokenReview call → FAILED (not INVALID)."""
    strategy = _make_strategy()

    with patch.object(strategy, "_do_token_review", side_effect=RuntimeError("connection refused")):
        result = await strategy.validate_token("some-token")

    assert result.status == AuthStatus.FAILED
    assert "error" in (result.error_message or "").lower()


# ---------------------------------------------------------------------------
# _do_token_review — direct unit tests with mocked kubernetes SDK
# ---------------------------------------------------------------------------


def test_do_token_review_success_returns_auth_result() -> None:
    """_do_token_review submits TokenReview and extracts SA principal."""
    mapping = {"orb-system:orb-worker": ["operator"]}
    strategy = _make_strategy(sa_role_mapping=mapping)

    # Construct a mock K8sClient / AuthenticationV1Api response.
    fake_response = _make_token_review_response(
        authenticated=True,
        username="system:serviceaccount:orb-system:orb-worker",
        uid="uid-42",
    )

    mock_auth_api_instance = MagicMock()
    mock_auth_api_instance.create_token_review.return_value = fake_response

    mock_k8s_client = MagicMock()
    mock_k8s_client.api_client = MagicMock()
    strategy._kubernetes_client = mock_k8s_client

    # Patch at kubernetes.client level since AuthenticationV1Api is
    # imported locally inside _do_token_review via "from kubernetes.client import ..."
    with patch("kubernetes.client.AuthenticationV1Api", return_value=mock_auth_api_instance):
        result = strategy._do_token_review("some-jwt")

    assert result.status == AuthStatus.SUCCESS
    assert result.user_id == "uid-42"
    assert "operator" in result.user_roles
    assert result.metadata["principal"] == "orb-system:orb-worker"
    mock_auth_api_instance.create_token_review.assert_called_once()


def test_do_token_review_not_authenticated_returns_invalid() -> None:
    """API server authenticates=False → INVALID result."""
    strategy = _make_strategy()

    fake_response = _make_token_review_response(
        authenticated=False,
        error="token has expired",
    )

    mock_auth_api_instance = MagicMock()
    mock_auth_api_instance.create_token_review.return_value = fake_response

    mock_k8s_client = MagicMock()
    mock_k8s_client.api_client = MagicMock()
    strategy._kubernetes_client = mock_k8s_client

    with patch("kubernetes.client.AuthenticationV1Api", return_value=mock_auth_api_instance):
        result = strategy._do_token_review("expired-jwt")

    assert result.status == AuthStatus.INVALID
    assert "token has expired" in (result.error_message or "")


def test_do_token_review_api_exception_raises() -> None:
    """SDK exception in _do_token_review is re-raised as RuntimeError."""
    strategy = _make_strategy()

    mock_auth_api_instance = MagicMock()
    mock_auth_api_instance.create_token_review.side_effect = Exception("network failure")

    mock_k8s_client = MagicMock()
    mock_k8s_client.api_client = MagicMock()
    strategy._kubernetes_client = mock_k8s_client

    with patch("kubernetes.client.AuthenticationV1Api", return_value=mock_auth_api_instance):
        with pytest.raises(RuntimeError, match="network failure"):
            strategy._do_token_review("token")


def test_do_token_review_passes_audiences_when_set() -> None:
    """When audiences is set, it is forwarded to V1TokenReviewSpec."""
    strategy = _make_strategy(audiences=["https://orb.example.com"])

    fake_response = _make_token_review_response(
        authenticated=True,
        username="system:serviceaccount:default:my-sa",
        uid="uid-1",
    )

    mock_auth_api_instance = MagicMock()
    mock_auth_api_instance.create_token_review.return_value = fake_response

    mock_k8s_client = MagicMock()
    mock_k8s_client.api_client = MagicMock()
    strategy._kubernetes_client = mock_k8s_client

    with (
        patch("kubernetes.client.AuthenticationV1Api", return_value=mock_auth_api_instance),
        patch("kubernetes.client.V1TokenReviewSpec") as mock_spec_cls,
    ):
        mock_spec_cls.return_value = SimpleNamespace(
            token="some-jwt", audiences=["https://orb.example.com"]
        )
        strategy._do_token_review("some-jwt")
        mock_spec_cls.assert_called_once_with(
            token="some-jwt", audiences=["https://orb.example.com"]
        )


# ---------------------------------------------------------------------------
# SA principal extraction and role mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "username,expected_principal",
    [
        ("system:serviceaccount:orb-system:orb-worker", "orb-system:orb-worker"),
        ("system:serviceaccount:default:my-sa", "default:my-sa"),
        ("admin", "admin"),  # non-SA username returned unchanged
        ("", ""),
    ],
)
def test_extract_sa_principal(username: str, expected_principal: str) -> None:
    assert KubeAuthStrategy._extract_sa_principal(username) == expected_principal


@pytest.mark.parametrize(
    "principal,mapping,expected_roles",
    [
        # Exact match
        ("orb-system:orb-admin", {"orb-system:orb-admin": ["admin"]}, ["admin"]),
        # Wildcard namespace match
        ("staging:orb-operator", {"*:orb-operator": ["operator"]}, ["operator"]),
        # Exact takes precedence over wildcard
        (
            "prod:orb-operator",
            {"prod:orb-operator": ["admin"], "*:orb-operator": ["operator"]},
            ["admin"],
        ),
        # No match → default
        ("unknown:sa", {}, ["user"]),
        # Non-SA username, no match
        ("alice", {}, ["user"]),
    ],
)
def test_map_principal_to_roles(principal: str, mapping: dict, expected_roles: list) -> None:
    strategy = _make_strategy(sa_role_mapping=mapping)
    assert strategy._map_principal_to_roles(principal) == expected_roles


def test_generate_permissions_admin() -> None:
    strategy = _make_strategy()
    perms = strategy._generate_permissions(["admin"])
    assert "hostfactory:*" in perms
    assert "system:*" in perms


def test_generate_permissions_operator() -> None:
    strategy = _make_strategy()
    perms = strategy._generate_permissions(["operator"])
    assert "hostfactory:request_machines" in perms
    assert "hostfactory:*" not in perms


def test_generate_permissions_user() -> None:
    strategy = _make_strategy()
    perms = strategy._generate_permissions(["user"])
    assert "hostfactory:list_templates" in perms
    assert "hostfactory:request_machines" not in perms


# ---------------------------------------------------------------------------
# refresh_token / revoke_token stubs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_returns_failed() -> None:
    strategy = _make_strategy()
    result = await strategy.refresh_token("refresh-token")
    assert result.status == AuthStatus.FAILED
    assert result.error_message is not None


@pytest.mark.asyncio
async def test_revoke_token_returns_true() -> None:
    strategy = _make_strategy()
    ok = await strategy.revoke_token("some-token")
    assert ok is True


# ---------------------------------------------------------------------------
# from_auth_config factory
# ---------------------------------------------------------------------------


def test_from_auth_config_extracts_sa_role_mapping() -> None:
    """from_auth_config picks up sa_role_mapping from provider_auth.kubernetes."""
    k8s_cfg = SimpleNamespace(
        sa_role_mapping={"orb-system:worker": ["operator"]},
        audiences=None,
    )
    provider_auth = SimpleNamespace(kubernetes=k8s_cfg)
    auth_config = SimpleNamespace(provider_auth=provider_auth)

    with patch(
        "orb.infrastructure.di.container.get_container",
        side_effect=Exception("no container"),
    ):
        strategy = KubeAuthStrategy.from_auth_config(auth_config)

    assert isinstance(strategy, KubeAuthStrategy)
    assert strategy._sa_role_mapping == {"orb-system:worker": ["operator"]}


def test_from_auth_config_defaults_when_no_k8s_sub_config() -> None:
    """from_auth_config handles absent provider_auth gracefully."""
    auth_config = SimpleNamespace(provider_auth=None)

    with patch(
        "orb.infrastructure.di.container.get_container",
        side_effect=Exception("no container"),
    ):
        strategy = KubeAuthStrategy.from_auth_config(auth_config)

    assert isinstance(strategy, KubeAuthStrategy)
    assert strategy._sa_role_mapping == {}
    assert strategy._audiences is None


def test_from_auth_config_uses_di_logger_when_available() -> None:
    """from_auth_config resolves LoggingPort from DI container when possible."""
    mock_logger = _make_logger()
    mock_container = MagicMock()
    mock_container.get.side_effect = lambda cls: mock_logger if "LoggingPort" in str(cls) else None

    auth_config = SimpleNamespace(provider_auth=None)

    with patch(
        "orb.infrastructure.di.container.get_container",
        return_value=mock_container,
    ):
        strategy = KubeAuthStrategy.from_auth_config(auth_config)

    assert isinstance(strategy, KubeAuthStrategy)


# ---------------------------------------------------------------------------
# Regression: empty/whitespace Bearer token rejected locally (Fix 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_empty_token_returns_failed() -> None:
    """An empty Bearer token must be rejected before reaching TokenReview."""
    strategy = _make_strategy()
    ctx = AuthContext(
        method="GET",
        path="/api/v1",
        headers={"authorization": "Bearer "},
        query_params={},
    )
    result = await strategy.authenticate(ctx)
    assert result.status == AuthStatus.FAILED
    assert result.error_message is not None
    assert "empty" in result.error_message.lower() or "token" in result.error_message.lower()


@pytest.mark.asyncio
async def test_authenticate_whitespace_only_token_returns_failed() -> None:
    """A whitespace-only Bearer token (e.g. 'Bearer    ') must be rejected locally."""
    strategy = _make_strategy()
    ctx = AuthContext(
        method="GET",
        path="/api/v1",
        headers={"authorization": "Bearer    "},
        query_params={},
    )
    result = await strategy.authenticate(ctx)
    assert result.status == AuthStatus.FAILED


@pytest.mark.asyncio
async def test_empty_token_does_not_reach_token_review() -> None:
    """validate_token must never be called with an empty token.

    This verifies fail-closed behaviour: even if an empty string somehow
    makes it through, the local guard fires before the TokenReview round-trip.
    """
    strategy = _make_strategy()

    with patch.object(strategy, "validate_token") as mock_validate:
        ctx = AuthContext(
            method="GET",
            path="/api/v1",
            headers={"authorization": "Bearer "},
            query_params={},
        )
        await strategy.authenticate(ctx)
        mock_validate.assert_not_called()


# ---------------------------------------------------------------------------
# Regression: from_auth_config wires enabled from config (Fix 7)
# ---------------------------------------------------------------------------


def test_from_auth_config_respects_enabled_false_from_config() -> None:
    """from_auth_config must wire enabled=False when k8s_auth_cfg.enabled is False."""
    k8s_cfg = SimpleNamespace(
        sa_role_mapping={},
        audiences=None,
        enabled=False,
    )
    provider_auth = SimpleNamespace(kubernetes=k8s_cfg)
    auth_config = SimpleNamespace(provider_auth=provider_auth)

    with patch(
        "orb.infrastructure.di.container.get_container",
        side_effect=Exception("no container"),
    ):
        strategy = KubeAuthStrategy.from_auth_config(auth_config)

    assert strategy.is_enabled() is False, (
        "from_auth_config must respect enabled=False from k8s_auth_cfg"
    )


def test_from_auth_config_defaults_enabled_true_when_no_config() -> None:
    """When no k8s sub-config is present, enabled defaults to True."""
    auth_config = SimpleNamespace(provider_auth=None)

    with patch(
        "orb.infrastructure.di.container.get_container",
        side_effect=Exception("no container"),
    ):
        strategy = KubeAuthStrategy.from_auth_config(auth_config)

    assert strategy.is_enabled() is True


def test_from_auth_config_defaults_enabled_true_when_config_has_no_enabled_attr() -> None:
    """When k8s_auth_cfg has no enabled attr, defaults to True."""
    k8s_cfg = SimpleNamespace(sa_role_mapping={}, audiences=None)
    # Deliberately do NOT set k8s_cfg.enabled
    provider_auth = SimpleNamespace(kubernetes=k8s_cfg)
    auth_config = SimpleNamespace(provider_auth=provider_auth)

    with patch(
        "orb.infrastructure.di.container.get_container",
        side_effect=Exception("no container"),
    ):
        strategy = KubeAuthStrategy.from_auth_config(auth_config)

    assert strategy.is_enabled() is True

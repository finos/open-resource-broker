"""Unit tests for get_current_user role resolution.

Covers the unauthenticated (no user_id on request.state) branches to verify
least-privilege defaults and confirm that auth-ENABLED paths are unaffected.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.api.dependencies import get_current_user

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    user_id: str | None = None,
    user_roles: list[str] | None = None,
    auth_result: Any = None,
    auth_enabled: bool | None = None,
    app_state_missing: bool = False,
    app_missing: bool = False,
) -> MagicMock:
    """Construct a minimal mock Request object.

    Args:
        user_id: Value set on ``request.state.user_id`` (None = absent).
        user_roles: Value set on ``request.state.user_roles``.
        auth_result: Value set on ``request.state.auth_result``.
        auth_enabled: Value set on ``request.app.state.auth_enabled``.
            If None the attribute is absent from app.state.
        app_state_missing: If True, ``request.app.state`` raises AttributeError.
        app_missing: If True, ``request.app`` is None.
    """
    request = MagicMock()

    # --- request.state ---
    state = MagicMock()
    if user_id is not None:
        state.user_id = user_id
    else:
        # Make getattr(state, "user_id", None) return None
        del state.user_id  # removes the auto-created mock attr
        type(state).__getattr__ = lambda self, name: None if name == "user_id" else MagicMock()

    if user_roles is not None:
        state.user_roles = user_roles
    else:
        state.user_roles = []

    state.auth_result = auth_result
    request.state = state

    # --- request.app ---
    if app_missing:
        request.app = None
    elif app_state_missing:
        app = MagicMock()
        del app.state
        request.app = app
    else:
        app_state = MagicMock()
        if auth_enabled is not None:
            app_state.auth_enabled = auth_enabled
        else:
            # Attribute absent — getattr should return the default (True)
            del app_state.auth_enabled
        app = MagicMock()
        app.state = app_state
        request.app = app

    return request


def _request_no_user_id(auth_enabled: bool | None = None) -> MagicMock:
    """Return a mock request with no user_id on state."""
    request = MagicMock()
    # Ensure getattr(request.state, "user_id", None) returns None
    state = MagicMock(spec=[])  # empty spec → any getattr returns None via spec
    request.state = state

    app_state = MagicMock(spec=[])
    if auth_enabled is not None:
        app_state.auth_enabled = auth_enabled
    app = MagicMock()
    app.state = app_state
    request.app = app
    return request


def _request_with_user(
    user_id: str = "alice",
    roles: list[str] | None = None,
    metadata: dict | None = None,
) -> MagicMock:
    """Return a mock request with a resolved user_id on state."""
    request = MagicMock()
    state = MagicMock()
    state.user_id = user_id
    state.user_roles = roles or []
    auth_result = MagicMock()
    auth_result.metadata = metadata or {}
    state.auth_result = auth_result
    request.state = state
    return request


# ---------------------------------------------------------------------------
# Tests: unauthenticated paths (no user_id) → always viewer
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestGetCurrentUserUnauthenticated:
    """When no user_id is on request.state the caller is always viewer."""

    def test_auth_disabled_no_user_id_returns_viewer(self):
        """auth_enabled=False, no user_id → role must be viewer (not admin).

        This is the security regression guard: the branch that previously
        returned role='admin' when auth was disabled has been removed.
        """
        request = _request_no_user_id(auth_enabled=False)
        user = get_current_user(request)

        assert user.role == "viewer", (
            "An unauthenticated caller on an auth-disabled deployment must get "
            "viewer, not admin — granting admin is a privilege-escalation bug."
        )
        assert user.username == "anonymous"

    def test_auth_enabled_excluded_path_returns_viewer(self):
        """auth_enabled=True, no user_id (excluded path) → role is viewer."""
        request = _request_no_user_id(auth_enabled=True)
        user = get_current_user(request)

        assert user.role == "viewer"
        assert user.username == "anonymous"

    def test_app_state_missing_returns_viewer(self):
        """request.app.state absent → fail-closed to viewer, no exception."""
        request = MagicMock()
        state = MagicMock(spec=[])  # no user_id attribute
        request.state = state

        # app exists but state attribute raises AttributeError
        app = object()  # plain object, no .state attribute
        request.app = app

        user = get_current_user(request)

        assert user.role == "viewer"
        assert user.username == "anonymous"

    def test_app_none_returns_viewer(self):
        """request.app is None → fail-closed to viewer, no exception."""
        request = MagicMock()
        state = MagicMock(spec=[])
        request.state = state
        request.app = None

        user = get_current_user(request)

        assert user.role == "viewer"
        assert user.username == "anonymous"

    def test_unauthenticated_viewer_has_only_read_permission(self):
        """Anonymous viewer has only 'read' permission, never mutate permissions."""
        request = _request_no_user_id(auth_enabled=False)
        user = get_current_user(request)

        assert user.permissions == ["read"]
        assert "create_template" not in user.permissions
        assert "delete_template" not in user.permissions
        assert "request_machines" not in user.permissions


# ---------------------------------------------------------------------------
# Tests: authenticated paths (user_id present) → real role resolution
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestGetCurrentUserAuthenticated:
    """Authenticated callers get their correct RBAC role — auth fix must not
    weaken this path."""

    def test_admin_role_claim_returns_admin(self):
        """User with admin role claim gets role='admin'."""
        request = _request_with_user(user_id="bob", roles=["admin"])
        user = get_current_user(request)

        assert user.role == "admin"
        assert user.username == "bob"

    def test_orb_admin_role_claim_returns_admin(self):
        """User with orb-admin claim maps to admin role."""
        request = _request_with_user(user_id="carol", roles=["orb-admin"])
        user = get_current_user(request)

        assert user.role == "admin"

    def test_operator_role_claim_returns_operator(self):
        """User with operator claim gets role='operator'."""
        request = _request_with_user(user_id="dave", roles=["operator"])
        user = get_current_user(request)

        assert user.role == "operator"

    def test_no_meaningful_role_claims_returns_viewer(self):
        """Authenticated user with no recognised role claims defaults to viewer."""
        request = _request_with_user(user_id="eve", roles=["some-unknown-group"])
        user = get_current_user(request)

        assert user.role == "viewer"

    def test_anonymous_sentinel_in_roles_is_filtered_out(self):
        """'anonymous' in user_roles (NoAuthStrategy sentinel) is ignored."""
        request = _request_with_user(user_id="frank", roles=["anonymous"])
        user = get_current_user(request)

        # 'anonymous' is not a meaningful role — defaults to viewer
        assert user.role == "viewer"

    def test_anonymous_sentinel_does_not_prevent_higher_role(self):
        """'anonymous' sentinel alongside a real role still resolves the real role."""
        request = _request_with_user(user_id="grace", roles=["anonymous", "admin"])
        user = get_current_user(request)

        assert user.role == "admin"

    def test_claims_from_auth_result_metadata_stored(self):
        """Claims extracted from auth_result.metadata are stored on CurrentUser."""
        meta = {"sub": "grace-id", "email": "grace@example.com"}
        request = _request_with_user(user_id="grace", roles=["viewer"], metadata=meta)
        user = get_current_user(request)

        assert user.claims == meta

    def test_empty_roles_list_returns_viewer(self):
        """Authenticated user with an empty roles list gets viewer (least privilege)."""
        request = _request_with_user(user_id="henry", roles=[])
        user = get_current_user(request)

        assert user.role == "viewer"

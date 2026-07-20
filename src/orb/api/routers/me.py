"""Authenticated-user introspection endpoint."""

from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, status
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/me", tags=["Auth"])


@router.get(
    "/",
    operation_id="getCurrentUser",
    summary="Current user identity and role",
    response_description="The authenticated caller's username, role, and derived permissions.",
)
async def get_me(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """
    Return the identity and capabilities of the authenticated caller.

    Returns 401 when the caller is unauthenticated (anonymous identity), so the
    UI can distinguish "not logged in" from "logged in as a viewer".

    Response shape::

        {
            "username": "alice",
            "role": "operator",
            "permissions": ["read", "request_machines", "return_machines", "cancel_request"]
        }

    Roles and their permissions:

    - **viewer** — read-only access: ``["read"]``
    - **operator** — machine lifecycle: ``["read", "request_machines",
      "return_machines", "cancel_request"]``
    - **admin** — full access including template CRUD: all operator permissions
      plus ``["create_template", "update_template", "delete_template"]``
    """
    if current_user.username == "anonymous":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return {
        "username": current_user.username,
        "role": current_user.role,
        "permissions": current_user.permissions,
    }

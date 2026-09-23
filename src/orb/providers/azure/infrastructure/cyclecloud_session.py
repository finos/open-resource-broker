"""CycleCloud infrastructure session context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import httpx


def _coerce_optional_bool(value: Any) -> Optional[bool]:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return bool(value)


@dataclass(frozen=True)
class CycleCloudCredentialData:
    """CycleCloud credential material resolved from a credential file."""

    url: Optional[str] = None
    verify_ssl: Optional[bool] = None
    auth_mode: Optional[str] = None
    username: Optional[str] = field(default=None, repr=False)
    password: Optional[str] = field(default=None, repr=False)
    bearer_token: Optional[str] = field(default=None, repr=False)
    aad_scope: Optional[str] = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> CycleCloudCredentialData:
        """Construct credential data from a flat config mapping."""
        return cls(
            url=data.get("url"),
            verify_ssl=_coerce_optional_bool(data.get("verify_ssl")),
            auth_mode=data.get("auth_mode"),
            username=data.get("username"),
            password=data.get("password"),
            bearer_token=data.get("bearer_token"),
            aad_scope=data.get("aad_scope"),
        )


@dataclass(frozen=True)
class CycleCloudSessionSettings:
    """Resolved CycleCloud transport and auth settings before session creation."""

    base_url: str
    verify_ssl: bool
    auth_mode: Optional[str]
    credential_data: CycleCloudCredentialData = field(repr=False)


@dataclass(frozen=True)
class AsyncCycleCloudSessionContext:
    """Resolved async CycleCloud HTTP session plus ORB-specific connection metadata."""

    client: httpx.AsyncClient = field(repr=False)
    base_url: str
    auth_mode: Optional[str]
    verify_ssl: bool

    def __repr__(self) -> str:
        """Return a safe repr that avoids leaking client internals or auth material."""
        return (
            "AsyncCycleCloudSessionContext("
            f"base_url={self.base_url!r}, "
            f"auth_mode={self.auth_mode!r}, "
            f"verify_ssl={self.verify_ssl!r})"
        )

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


def _mapping_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


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
            url=_mapping_value(data, "cyclecloud_url", "url"),
            verify_ssl=_coerce_optional_bool(
                _mapping_value(data, "cyclecloud_verify_ssl", "verify_ssl")
            ),
            auth_mode=_mapping_value(data, "cyclecloud_auth_mode", "auth_mode"),
            username=_mapping_value(data, "cyclecloud_username", "username"),
            password=_mapping_value(data, "cyclecloud_password", "password"),
            bearer_token=_mapping_value(data, "cyclecloud_bearer_token", "bearer_token"),
            aad_scope=_mapping_value(data, "cyclecloud_aad_scope", "aad_scope"),
        )


@dataclass(frozen=True)
class CycleCloudSessionSettings:
    """Resolved CycleCloud transport and auth settings before session creation."""

    base_url: str
    verify_ssl: bool
    auth_mode: Optional[str]
    credential_path: Optional[str]


@dataclass(frozen=True)
class CycleCloudRequestContext:
    """Typed CycleCloud request/follow-up context carried through handler flows."""

    cluster_name: Optional[str] = None
    node_array: Optional[str] = None
    node_ids: tuple[str, ...] = ()
    operation_id: Optional[str] = None
    operation_location: Optional[str] = None
    added_count: Optional[int] = None
    cyclecloud_url: Optional[str] = None
    cyclecloud_credential_path: Optional[str] = None
    cyclecloud_verify_ssl: Optional[bool] = None
    cyclecloud_auth_mode: Optional[str] = None
    cyclecloud_aad_scope: Optional[str] = None

    @classmethod
    def from_mapping(cls, data: Optional[dict[str, Any]]) -> CycleCloudRequestContext:
        """Construct a request context from an optional metadata mapping."""
        if not data:
            return cls()

        raw_node_ids = data.get("node_ids") or ()
        if isinstance(raw_node_ids, (list, tuple)):
            node_ids = tuple(str(node_id) for node_id in raw_node_ids if node_id not in (None, ""))
        else:
            node_ids = ()

        raw_added_count = data.get("added_count")
        added_count = int(raw_added_count) if raw_added_count not in (None, "") else None

        return cls(
            cluster_name=data.get("cluster_name"),
            node_array=data.get("node_array"),
            node_ids=node_ids,
            operation_id=data.get("operation_id"),
            operation_location=data.get("operation_location"),
            added_count=added_count,
            cyclecloud_url=data.get("cyclecloud_url"),
            cyclecloud_credential_path=data.get("cyclecloud_credential_path"),
            cyclecloud_verify_ssl=_coerce_optional_bool(data.get("cyclecloud_verify_ssl")),
            cyclecloud_auth_mode=data.get("cyclecloud_auth_mode"),
            cyclecloud_aad_scope=data.get("cyclecloud_aad_scope"),
        )

    def to_metadata(self) -> dict[str, Any]:
        """Serialize non-empty fields to a metadata dict for transport."""
        metadata: dict[str, Any] = {}
        if self.cluster_name not in (None, ""):
            metadata["cluster_name"] = self.cluster_name
        if self.node_array not in (None, ""):
            metadata["node_array"] = self.node_array
        if self.node_ids:
            metadata["node_ids"] = list(self.node_ids)
        if self.operation_id not in (None, ""):
            metadata["operation_id"] = self.operation_id
        if self.operation_location not in (None, ""):
            metadata["operation_location"] = self.operation_location
        if self.added_count is not None:
            metadata["added_count"] = self.added_count
        if self.cyclecloud_url not in (None, ""):
            metadata["cyclecloud_url"] = self.cyclecloud_url
        if self.cyclecloud_credential_path not in (None, ""):
            metadata["cyclecloud_credential_path"] = self.cyclecloud_credential_path
        if self.cyclecloud_verify_ssl not in (None, ""):
            metadata["cyclecloud_verify_ssl"] = self.cyclecloud_verify_ssl
        if self.cyclecloud_auth_mode not in (None, ""):
            metadata["cyclecloud_auth_mode"] = self.cyclecloud_auth_mode
        if self.cyclecloud_aad_scope not in (None, ""):
            metadata["cyclecloud_aad_scope"] = self.cyclecloud_aad_scope
        return metadata


@dataclass(frozen=True)
class AsyncCycleCloudSessionContext:
    """Resolved async CycleCloud HTTP session plus ORB-specific connection metadata."""

    client: httpx.AsyncClient = field(repr=False)
    base_url: str
    auth_mode: Optional[str]
    credential_path: Optional[str]
    verify_ssl: bool

    def __repr__(self) -> str:
        """Return a safe repr that avoids leaking client internals or auth material."""
        return (
            "AsyncCycleCloudSessionContext("
            f"base_url={self.base_url!r}, "
            f"auth_mode={self.auth_mode!r}, "
            f"credential_path={self.credential_path!r}, "
            f"verify_ssl={self.verify_ssl!r})"
        )

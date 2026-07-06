"""ORB REST client. Single async httpx client, typed wrappers per endpoint."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

ORB_BASE_URL = os.getenv("ORB_BASE_URL", "http://localhost:8000")
# In embedded mode ORB is mounted at /orb inside the Reflex process, so the
# effective API root is /orb/api/v1 and health/info live at /orb/health etc.
# Override both env vars when talking to a standalone ORB served at root.
ORB_API_PREFIX = os.getenv("ORB_API_PREFIX", "/orb/api/v1")
ORB_ROOT_PREFIX = os.getenv("ORB_ROOT_PREFIX", "/orb")
TIMEOUT = httpx.Timeout(30.0, connect=5.0)

# Always request the canonical (snake_case) shape from ORB regardless of
# the server's active scheduler strategy. ORB exposes a per-request
# override via the X-ORB-Scheduler header (see
# orb.api.dependencies.get_request_formatter / get_request_scheduler).
_DEFAULT_HEADERS = {"X-ORB-Scheduler": "default"}


def _loopback_token() -> str | None:
    """Read the loopback-admin token written by the daemon at start time.

    The UI backend runs in the same process (embedded mode) or on the same
    host (split mode) as ORB, so it can read the token file that
    ``server_daemon._write_token_file`` places next to the PID file.
    Sending the token as ``Authorization: Bearer <token>`` promotes UI
    calls from anonymous ``viewer`` to loopback ``admin`` — matching what
    ``orb server reload`` already does.

    Returns None when the file is missing (auth disabled or foreground
    ``orb serve`` without daemon).  Callers fall back to unauthenticated
    requests, which still work for read-only endpoints.
    """
    # Cheap discovery — mirrors dev-tools flow: prefer the explicit env,
    # fall back to ``work/server/orb-server.token`` under platform-dirs.
    override = os.getenv("ORB_LOOPBACK_TOKEN_FILE")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    try:
        from orb.config.platform_dirs import get_work_location

        candidates.append(get_work_location() / "server" / "orb-server.token")
    except Exception:
        return None
    for token_file in candidates:
        try:
            if token_file.is_file():
                token = token_file.read_text(encoding="ascii").strip()
                if token:
                    return token
        except OSError:
            continue
    return None


def _headers() -> dict[str, str]:
    headers = dict(_DEFAULT_HEADERS)
    token = _loopback_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=ORB_BASE_URL,
        timeout=TIMEOUT,
        headers=_headers(),
    )


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    async with _client() as c:
        r = await c.get(f"{ORB_API_PREFIX}{path}", params=params)
        r.raise_for_status()
        return r.json()


async def _post(path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
    async with _client() as c:
        r = await c.post(f"{ORB_API_PREFIX}{path}", json=json)
        r.raise_for_status()
        return r.json()


async def _delete(path: str) -> dict[str, Any]:
    async with _client() as c:
        r = await c.delete(f"{ORB_API_PREFIX}{path}")
        r.raise_for_status()
        return r.json() if r.content else {}


async def _put(path: str, json: dict[str, Any]) -> dict[str, Any]:
    async with _client() as c:
        r = await c.put(f"{ORB_API_PREFIX}{path}", json=json)
        r.raise_for_status()
        return r.json()


# Health/info — top-level endpoints, but prefixed by ORB_ROOT_PREFIX in
# embedded mode where ORB is mounted at /orb.
async def get_health() -> dict[str, Any]:
    async with _client() as c:
        r = await c.get(f"{ORB_ROOT_PREFIX}/health")
        r.raise_for_status()
        return r.json()


async def get_info() -> dict[str, Any]:
    async with _client() as c:
        r = await c.get(f"{ORB_ROOT_PREFIX}/info")
        r.raise_for_status()
        return r.json()


async def get_me() -> dict[str, Any]:
    async with _client() as c:
        r = await c.get(f"{ORB_ROOT_PREFIX}/api/v1/me")
        if r.status_code == 404:
            # /me endpoint not present yet — degrade gracefully
            return {
                "username": "anonymous",
                "role": "admin",
                "permissions": [
                    "read",
                    "request_machines",
                    "return_machines",
                    "cancel_request",
                    "create_template",
                    "update_template",
                    "delete_template",
                ],
            }
        r.raise_for_status()
        return r.json()


# Machines
async def list_machines(
    status: str | None = None,
    provider_name: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    elif offset:
        params["offset"] = offset
    if status:
        params["status"] = status
    if provider_name:
        params["provider_name"] = provider_name
    if q:
        params["q"] = q
    if sort:
        params["sort"] = sort
    return await _get("/machines/", params=params)


async def get_machine(machine_id: str) -> dict[str, Any]:
    return await _get(f"/machines/{machine_id}")


async def sync_machine(machine_id: str) -> dict[str, Any]:
    """Refresh a single machine from the provider.

    Hits GET /machines/{id}/status which performs a read-through sync —
    one DescribeInstances per call. Returns the updated MachineDTO plus
    ``synced: bool`` and optional ``sync_error`` in the response body.
    """
    return await _get(f"/machines/{machine_id}/status")


async def request_machines(body: dict[str, Any]) -> dict[str, Any]:
    return await _post("/machines/request", json=body)


async def return_machines(body: dict[str, Any]) -> dict[str, Any]:
    return await _post("/machines/return", json=body)


# Requests
async def list_requests(
    status: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    elif offset:
        params["offset"] = offset
    if status:
        params["status"] = status
    if q:
        params["q"] = q
    if sort:
        params["sort"] = sort
    return await _get("/requests/", params=params)


async def get_request(request_id: str) -> dict[str, Any]:
    # ORB exposes per-request detail under .../{id}/status (not .../{id}).
    return await _get(f"/requests/{request_id}/status")


async def batch_get_request_status(request_ids: list[str], verbose: bool = True) -> dict[str, Any]:
    """Read-through-sync a batch of requests in one POST.

    Server iterates ``request_ids`` and runs the same per-request sync
    path as ``GET /{id}/status`` against each. Per-id failures surface
    as ``{"request_id": ..., "error": ...}`` entries in the response
    list rather than failing the whole call.
    """
    return await _post("/requests/status", json={"request_ids": request_ids, "verbose": verbose})


async def list_return_requests(
    status: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    elif offset:
        params["offset"] = offset
    if status:
        params["status"] = status
    if q:
        params["q"] = q
    if sort:
        params["sort"] = sort
    return await _get("/requests/return", params=params)


async def cancel_request(request_id: str) -> dict[str, Any]:
    return await _delete(f"/requests/{request_id}")


# Templates
async def list_templates(
    provider_api: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    elif offset:
        params["offset"] = offset
    if provider_api:
        params["provider_api"] = provider_api
    if q:
        params["q"] = q
    if sort:
        params["sort"] = sort
    return await _get("/templates/", params=params)


async def get_template(template_id: str) -> dict[str, Any]:
    return await _get(f"/templates/{template_id}")


async def create_template(body: dict[str, Any]) -> dict[str, Any]:
    return await _post("/templates/", json=body)


async def update_template(template_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return await _put(f"/templates/{template_id}", json=body)


async def delete_template(template_id: str) -> dict[str, Any]:
    return await _delete(f"/templates/{template_id}")


async def validate_template(body: dict[str, Any]) -> dict[str, Any]:
    return await _post("/templates/validate", json=body)


async def refresh_templates() -> dict[str, Any]:
    return await _post("/templates/refresh")


async def generate_templates(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Generate example templates for all (or a specific) provider.

    ``body`` defaults to ``{"all_providers": True}`` when omitted, which
    generates templates for every active provider without force-overwrite.
    """
    return await _post("/templates/generate", json=body or {"all_providers": True})


async def wipe_database() -> dict[str, Any]:
    """Truncate all ORB DB tables.

    Requires server config ``allow_destructive_admin=true`` and a
    non-production environment.  Sends the mandatory confirmation token so
    the server accepts the request.

    Raises:
        httpx.HTTPStatusError: 403 if the feature is disabled or env is
            production; 400 if the confirmation token is wrong (should
            not happen with this implementation).
    """
    return await _post("/admin/database/wipe", json={"confirm": "WIPE"})


async def init_orb(body: dict[str, Any]) -> dict[str, Any]:
    """Initialize ORB: create config file, data directories, refresh templates.

    Requires server config ``allow_destructive_admin=true`` and a
    non-production environment.

    Raises:
        httpx.HTTPStatusError: 403 if the feature is disabled or env is
            production; 400 if the confirmation token is wrong.
    """
    payload = {"confirm": "INIT", **body}
    return await _post("/admin/init", json=payload)


async def get_dashboard_summary() -> dict[str, Any]:
    """Return pre-rolled-up dashboard counts from the aggregate endpoint.

    Response shape::

        {
            "machines":  {"total": int, "by_status": {status: int}},
            "requests":  {"total": int, "in_flight": int, "by_status": {status: int}},
            "templates": {"total": int, "by_provider_api": {api: int}},
            "recent_activity": [{"request_id", "status", "request_type",
                                  "template_id", "created_at",
                                  "successful_count", "requested_count"}, ...],
        }

    Raises:
        httpx.HTTPStatusError: 404 if the /system/dashboard endpoint is not
            registered (e.g. the server is running an older version).
    """
    return await _get("/system/dashboard")


async def get_config(source: str | None = None) -> dict[str, Any]:
    """Return the full effective configuration tree.

    Pass ``source="file"`` to receive the raw on-disk dict before Pydantic
    hydration (used by the Config page to distinguish file-set keys from
    compiled-in defaults).
    """
    params: dict[str, Any] = {}
    if source:
        params["source"] = source
    return await _get("/config/", params=params if params else None)


async def get_config_value(key: str) -> Any:
    """Return a single configuration value by dot-notation key."""
    data = await _get(f"/config/{key}")
    return data.get("value")


async def set_config_value(key: str, value: Any) -> dict[str, Any]:
    """Set a configuration value in memory.

    Returns the response dict including ``value``, ``persisted``, and ``note``.
    """
    return await _put(f"/config/{key}", json={"value": value})


async def reload_config() -> dict[str, Any]:
    """Reload configuration from disk.

    Returns ``{"reloaded": true, "message": "..."}``.
    """
    return await _post("/admin/reload-config")


async def save_config(path: str | None = None) -> dict[str, Any]:
    """Persist in-memory config to disk.

    Returns ``{"persisted": true, "path": "<file_path>"}``.
    """
    body: dict[str, Any] = {}
    if path:
        body["path"] = path
    return await _post("/config/save", json=body)


async def get_config_sources() -> dict[str, Any]:
    """Return configuration source information."""
    return await _get("/config/sources")


async def get_provider_schemas() -> dict[str, list[dict[str, Any]]]:
    """Return all registered provider UI column schemas keyed by provider name.

    Calls ``GET /api/v1/providers/schemas`` which returns a JSON object whose
    keys are provider names (e.g. ``"aws"``) and whose values are arrays of
    UIColumnDescriptor dicts.  An empty dict is returned when the endpoint is
    absent or returns an empty body.
    """
    try:
        result = await _get("/providers/schemas")
        if isinstance(result, dict):
            return result
        return {}
    except Exception:
        return {}


async def subscribe_events(event_types=None):
    """Yield (event_type, data) from the backend SSE stream.

    ``event_types`` is an optional iterable that filters server-side via
    ?type=a,b,c.
    """
    from .sse_client import stream_sse

    params = ""
    if event_types:
        params = "?type=" + ",".join(sorted(event_types))
    url = f"{ORB_BASE_URL}{ORB_ROOT_PREFIX}/api/v1/events{params}"
    async for evt, data in stream_sse(url, headers=_headers()):
        yield evt, data

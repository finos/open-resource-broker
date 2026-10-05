"""Catalog-driven MCP server for Open Resource Broker.

This module exposes the operation catalog as Model Context Protocol tools.
Every operation the broker declares once in :data:`OPERATION_CATALOG` and marks
as exposed on :attr:`Interface.MCP` becomes an MCP tool automatically: the tool
name is the catalog key, its input schema is derived from the operation's input
DTO, and its result body is the operation output rendered through the shared
:class:`ResponseFormattingService` — the same seam the CLI, REST, and SDK
adapters render through. The tool set therefore never drifts from the catalog,
and a tool's body stays in lockstep with the other interfaces for the same
operation.

The server is built on the low-level MCP SDK server so tool registration is
data-driven rather than a hand-written function per tool. Two thin entrypoints
run it over the standard transports: stdio (for local subprocess clients) and
Streamable HTTP (for networked clients).
"""

from __future__ import annotations

import dataclasses
import json
import typing
from typing import Any, Union, get_args, get_origin

import mcp.types as mcp_types
from mcp.server.lowlevel import Server

from orb._package import PACKAGE_NAME, __version__
from orb.interface.catalog import (
    OPERATION_CATALOG,
    CatalogEntry,
    Interface,
    bind_from_mapping,
)
from orb.interface.response_formatting_service import ResponseFormattingService

# JSON-schema type name for each Python scalar/collection the input DTOs use.
# Anything not in this map falls back to a permissive (unconstrained) property.
_JSON_TYPE_BY_PYTHON: dict[type, str] = {
    str: "string",
    bool: "boolean",  # checked before int: bool is a subclass of int
    int: "integer",
    float: "number",
    list: "array",
    dict: "object",
}


def _unwrap_optional(annotation: Any) -> Any:
    """Return ``T`` for an ``Optional[T]`` / ``T | None`` annotation, else the input.

    Only single-``None`` unions are unwrapped; a wider union is left untouched so
    it falls through to the permissive schema branch.
    """
    if get_origin(annotation) is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _is_optional(annotation: Any) -> bool:
    """True when the annotation is a single-``None`` union (``Optional[T]``).

    Such a field legitimately accepts an explicit JSON ``null``, so its schema
    must advertise the null type — otherwise a client that serialises an unset
    optional as ``null`` is rejected by input validation before the handler runs.
    """
    if get_origin(annotation) is Union:
        args = get_args(annotation)
        return type(None) in args and len([a for a in args if a is not type(None)]) == 1
    return False


def _json_type_for(annotation: Any) -> str | None:
    """Map a (possibly Optional/generic) annotation to a JSON-schema type name.

    Returns ``None`` when no simple mapping applies, in which case the property is
    emitted without a ``type`` constraint (permissive).
    """
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)
    base = origin if origin is not None else annotation
    if not isinstance(base, type):
        return None
    # bool must be tested before int (bool is a subclass of int).
    if base is bool:
        return "boolean"
    for python_type, json_name in _JSON_TYPE_BY_PYTHON.items():
        if base is python_type:
            return json_name
    if issubclass(base, bool):
        return "boolean"
    if issubclass(base, int):
        return "integer"
    if issubclass(base, str):
        return "string"
    return None


def schema_from_input_dto(input_dto: type) -> dict[str, Any]:
    """Build a JSON-schema object describing an input DTO's constructor fields.

    Each dataclass field becomes a property; its type is mapped from the field
    annotation (Optional unwrapped, basic scalars and collections recognised).
    Fields with neither a default nor a default factory are marked required.
    ``additionalProperties`` stays permissive so a caller may pass extra keys —
    :func:`bind_from_mapping` drops any that do not name a field.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    # Resolve string annotations (the catalog DTOs use ``from __future__ import
    # annotations``) to real types where possible; fall back to raw field types.
    try:
        hints = typing.get_type_hints(input_dto)
    except Exception:
        hints = {}

    for field in dataclasses.fields(input_dto):
        annotation = hints.get(field.name, field.type)
        json_type = _json_type_for(annotation)
        prop: dict[str, Any] = {}
        if json_type is not None:
            # An Optional[T] field accepts an explicit null, so advertise both
            # the scalar type and null; a required field takes the bare type.
            if _is_optional(annotation):
                prop["type"] = [json_type, "null"]
            else:
                prop["type"] = json_type
            if json_type == "array":
                prop["items"] = {"type": "string"}
        properties[field.name] = prop

        has_default = field.default is not dataclasses.MISSING
        has_default_factory = field.default_factory is not dataclasses.MISSING
        if not has_default and not has_default_factory:
            required.append(field.name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }
    if required:
        schema["required"] = required
    return schema


def _mcp_entries() -> list[CatalogEntry[Any, Any]]:
    """Catalog entries exposed on the MCP interface, ordered by key."""
    return [
        entry for _, entry in sorted(OPERATION_CATALOG.items()) if Interface.MCP in entry.exposed_on
    ]


def list_catalog_tools() -> list[mcp_types.Tool]:
    """Return the MCP tool definitions derived from the operation catalog.

    Pure catalog derivation with no container or transport: building each tool
    resolves its input DTO into a JSON schema, so this doubles as an offline
    check that every MCP-exposed operation yields a valid tool definition.
    """
    return [_tool_for(entry) for entry in _mcp_entries()]


def _tool_for(entry: CatalogEntry[Any, Any]) -> mcp_types.Tool:
    """Build the MCP tool definition for a catalog entry."""
    orchestrator_name = entry.orchestrator.__name__
    description = (
        f"{entry.key} — dispatched through {orchestrator_name}. "
        f"Returns the operation result rendered as the broker's canonical body."
    )
    return mcp_types.Tool(
        name=entry.key,
        description=description,
        inputSchema=schema_from_input_dto(entry.input_dto),
    )


def _error_result(message: str) -> mcp_types.CallToolResult:
    """A tool-level error result (isError=True), not a protocol error."""
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=message)],
        isError=True,
    )


# Minimum RBAC role required to invoke each MCP-exposed tool once the
# Streamable HTTP transport has authenticated a caller. Mirrors the role each
# operation's REST (or, for the provider/CLI-only tools, its intent) requires:
# the mutating machine/request operations require "operator"; every read-only
# tool requires "viewer", the same floor REST enforces on its read routes.
# Every catalog entry exposed on Interface.MCP must have an entry here —
# ``test_mcp_catalog_server.py`` asserts this so a newly added tool fails that
# test until classified, rather than defaulting to an open role. Irrelevant to
# stdio, which has no caller identity to check.
_MCP_TOOL_MIN_ROLE: dict[str, str] = {
    "request_machines": "operator",
    "return_machines": "operator",
    "get_request_status": "viewer",
    "list_requests": "viewer",
    "list_return_requests": "viewer",
    "cancel_request": "operator",
    "list_machines": "viewer",
    "get_machine": "viewer",
    "stop_machines": "operator",
    "start_machines": "operator",
    "list_templates": "viewer",
    "get_template": "viewer",
    "validate_template": "viewer",
    "get_provider_health": "viewer",
    "list_providers": "viewer",
    "get_provider_config": "viewer",
    "get_provider_metrics": "viewer",
}

# Role required for a tool with no entry above. A tool only reaches this
# fallback if it is registered on the catalog's MCP interface without being
# added to _MCP_TOOL_MIN_ROLE; defaulting to "operator" keeps such a gap from
# silently granting unauthenticated-equivalent viewer access.
_MCP_DEFAULT_MIN_ROLE = "operator"


def _authorization_denial(server: Server, tool_name: str) -> mcp_types.CallToolResult | None:
    """Return a denial result when the HTTP caller's role is below the tool's minimum.

    Reads the identity ``AuthMiddleware`` already resolved onto the Streamable
    HTTP request's ``state`` — the same place REST's ``require_role``
    dependency reads it from — through the Starlette ``Request`` the
    Streamable HTTP transport attaches to the current MCP request context.

    Returns ``None`` (call permitted) when there is no HTTP request in scope:
    the stdio transport never attaches one, and a ``call_tool`` handler
    invoked directly (as the unit tests do) does not either, so both keep
    today's unrestricted behavior. Also returns ``None`` when the resolved
    role meets the tool's minimum.
    """
    try:
        request = server.request_context.request
    except LookupError:
        return None
    if request is None:
        return None

    from orb.api.dependencies import _ROLE_RANK, _resolve_role

    raw_roles: list[str] = list(getattr(request.state, "user_roles", None) or [])
    meaningful_roles = [r for r in raw_roles if r.lower() != "anonymous"]
    role = _resolve_role(meaningful_roles) if meaningful_roles else "viewer"
    min_role = _MCP_TOOL_MIN_ROLE.get(tool_name, _MCP_DEFAULT_MIN_ROLE)
    if _ROLE_RANK.get(role, 0) < _ROLE_RANK[min_role]:
        return _error_result(f"{tool_name}: insufficient permissions (requires {min_role} role)")
    return None


def build_server(container: Any) -> Server:
    """Build a low-level MCP :class:`Server` wired to the operation catalog.

    ``container`` is the DI container from which each tool call resolves its
    orchestrator and the shared :class:`ResponseFormattingService`. Tools are
    registered data-driven from :data:`OPERATION_CATALOG`; adding an MCP-exposed
    operation to the catalog adds a tool here with no further change.
    """
    server: Server = Server(PACKAGE_NAME, version=__version__)

    @server.list_tools()
    async def list_tools() -> list[mcp_types.Tool]:
        return list_catalog_tools()

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> list[mcp_types.TextContent] | mcp_types.CallToolResult:
        entry = OPERATION_CATALOG.get(name)
        if entry is None or Interface.MCP not in entry.exposed_on:
            return _error_result(f"Unknown tool: {name}")

        denial = _authorization_denial(server, name)
        if denial is not None:
            return denial

        try:
            dto = bind_from_mapping(entry, arguments or {})
            orchestrator = container.get(entry.orchestrator)
            formatter = container.get(ResponseFormattingService)
            result = await orchestrator.execute(dto)
            body = entry.renderer_for(Interface.MCP)(formatter, result).data
        except Exception as exc:  # noqa: BLE001 — surfaced to the client as isError
            return _error_result(f"{name} failed: {exc}")

        text = json.dumps(body, default=str)
        return [mcp_types.TextContent(type="text", text=text)]

    return server


async def run_stdio(container: Any) -> None:
    """Serve the catalog MCP server over stdio until the client disconnects."""
    from mcp.server.stdio import stdio_server

    server = build_server(container)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run_streamable_http(
    container: Any,
    host: str = "127.0.0.1",
    port: int = 8080,
    path: str = "/mcp",
    server_config: Any = None,
) -> None:
    """Serve the catalog MCP server over Streamable HTTP.

    Wraps the SDK's :class:`StreamableHTTPSessionManager` in a minimal Starlette
    app mounted at ``path`` (default ``/mcp``) and runs it under uvicorn. Blocks
    until the server is stopped.

    When ``server_config.auth.enabled`` is true, every request to the mounted
    path is authenticated with the same strategy and bearer-token validation
    REST uses: an :class:`AuthMiddleware` built over the configured auth
    strategy rejects a missing or invalid credential before it reaches a
    tool. Per-tool role enforcement (see :data:`_MCP_TOOL_MIN_ROLE`) happens
    inside :func:`build_server`'s ``call_tool`` handler, mirroring each
    tool's REST equivalent's ``require_role``.

    When auth is disabled (or ``server_config`` is not supplied), callers are
    treated as anonymous viewers — the same default REST's
    ``get_current_user`` falls back to — so read-only tools still work but
    every mutating tool is refused for every caller. Binding to a
    non-loopback host with auth disabled logs a warning, matching the REST
    server's posture for the same combination.
    """
    import contextlib
    from collections.abc import AsyncIterator

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.types import Receive, Scope, Send

    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    server = build_server(container)
    session_manager = StreamableHTTPSessionManager(app=server)

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    app = Starlette(
        routes=[Mount(path, app=handle_mcp)],
        lifespan=lifespan,
    )

    auth_config = getattr(server_config, "auth", None)
    _LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
    if auth_config is not None and auth_config.enabled:
        from orb.api.middleware.auth_middleware import AuthMiddleware
        from orb.infrastructure.auth.registry import get_auth_registry

        auth_strategy = get_auth_registry().get_strategy(auth_config.strategy, auth_config)
        app.add_middleware(
            AuthMiddleware,
            auth_port=auth_strategy,
            require_auth=True,
            excluded_paths=[],
            trusted_proxies=getattr(server_config, "trusted_proxies", None),
        )
        logger.info(
            "MCP Streamable HTTP transport authentication enabled with strategy: %s",
            auth_strategy.get_strategy_name(),
        )
    elif host not in _LOOPBACK_HOSTS:
        logger.warning(
            "SECURITY WARNING: MCP authentication is DISABLED and the server is bound to "
            "'%s' (non-loopback). Every caller is treated as an anonymous viewer, so "
            "mutating tools (request_machines, return_machines, cancel_request, "
            "stop_machines, start_machines) are refused, but read-only tools are reachable "
            "without credentials. Enable authentication (server.auth.enabled=true) before "
            "exposing this transport on a network interface.",
            host,
        )

    uvicorn.run(app, host=host, port=port)


def _flush_telemetry() -> None:
    """Flush OTel providers on MCP server shutdown (best-effort, idempotent)."""
    from orb.infrastructure.logging.logger import get_logger

    try:
        from orb.bootstrap.telemetry import shutdown_telemetry

        shutdown_telemetry()
    except Exception as exc:  # noqa: BLE001 — cleanup must never block shutdown
        get_logger(__name__).debug(
            "telemetry flush during MCP shutdown failed: %s (ignored, best-effort cleanup)",
            exc,
            exc_info=True,
        )


async def handle_mcp_serve(args: Any) -> dict[str, Any]:
    """Start the catalog-driven MCP server over the requested transport.

    Bootstraps the application so the DI container has every provider, handler,
    and configuration registered, then serves the catalog tools over stdio or
    Streamable HTTP. The ``http`` transport blocks in a worker thread (uvicorn
    manages its own event loop) so it does not collide with the CLI's loop.
    """
    import asyncio

    from orb.bootstrap import Application
    from orb.infrastructure.logging.logger import get_logger

    logger = get_logger(__name__)

    transport = getattr(args, "transport", "stdio")
    if transport == "streamable-http":
        transport = "http"
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)
    path = getattr(args, "path", "/mcp")

    app = Application()
    if not await app.initialize():
        raise RuntimeError("Failed to initialize ORB application for MCP server")
    # Reuse the wired container from the Application instance rather than calling
    # get_container() again (service-locator avoided).
    app._ensure_container()
    container = app._container

    try:
        if transport == "http":
            # Reuse the REST server's auth configuration (server.auth) so the
            # Streamable HTTP transport is authenticated with the same strategy
            # an operator already configured for the REST API. Falls back to
            # None (treated as auth-disabled) if the config cannot be read —
            # e.g. the container passed in has no ConfigurationPort, as in
            # tests that exercise transport dispatch without a real app.
            server_config = None
            try:
                from orb.config.schemas.server_schema import ServerConfig
                from orb.domain.base.ports.configuration_port import ConfigurationPort

                server_config = container.get(ConfigurationPort).get_typed(ServerConfig)
            except Exception as exc:  # noqa: BLE001 — fall back to auth-disabled semantics
                logger.debug("MCP server config lookup skipped: %s", exc)

            logger.info("Starting MCP server over Streamable HTTP on %s:%s%s", host, port, path)
            await asyncio.get_running_loop().run_in_executor(
                None, run_streamable_http, container, host, port, path, server_config
            )
            return {"message": f"MCP server stopped ({host}:{port}{path})"}
        logger.info("Starting MCP server over stdio")
        await run_stdio(container)
        return {"message": "MCP server stopped (stdio)"}
    finally:
        _flush_telemetry()


async def handle_mcp_validate(args: Any) -> Any:
    """Offline check that every MCP-exposed catalog entry yields a valid tool.

    Builds the tool set straight from the catalog — no client or server is spun
    up — and verifies each tool has a resolvable object input schema. Prints a
    summary of the tool count and names on success (exit 0) or lists the
    problems and returns a non-zero exit code, so it is safe to run in CI.
    """
    from orb.application.dto.interface_response import InterfaceResponse

    del args  # offline: no arguments influence the catalog-derived tool set

    problems: list[str] = []
    tools = list_catalog_tools()
    for tool in tools:
        schema = tool.inputSchema
        if not isinstance(schema, dict) or schema.get("type") != "object":
            problems.append(f"{tool.name}: input schema is not a JSON object schema")
        elif not isinstance(schema.get("properties"), dict):
            problems.append(f"{tool.name}: input schema has no properties object")

    tool_names = [tool.name for tool in tools]
    valid = not problems
    result: dict[str, Any] = {
        "valid": valid,
        "tool_count": len(tools),
        "tools": tool_names,
    }
    if problems:
        result["problems"] = problems
    return InterfaceResponse(data=result, exit_code=0 if valid else 1)

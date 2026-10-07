"""Unit tests for the catalog MCP server lifecycle and CLI handlers.

These exercise the transport wiring and CLI-facing handlers of the catalog MCP
server without any real network, stdio, or event-loop-blocking server: the
``serve`` handler's transport dispatch, the stdio and Streamable HTTP runners'
wiring, the telemetry-flush cleanup, and the offline ``validate`` handler. Every
transport boundary is mocked so nothing binds a port or reads a stream.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, sentinel

import mcp.types as mcp_types
import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.interface.catalog import OPERATION_CATALOG, Interface
from orb.interface.mcp import catalog_server


def _mcp_tool_names() -> list[str]:
    """Catalog keys exposed on the MCP interface, sorted by key."""
    return [
        key for key, entry in sorted(OPERATION_CATALOG.items()) if Interface.MCP in entry.exposed_on
    ]


# --------------------------------------------------------------------------- #
# handle_mcp_validate                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_validate_reports_all_catalog_tools_as_valid() -> None:
    """validate returns valid=True with the full MCP tool set and exit 0."""
    response = await catalog_server.handle_mcp_validate(SimpleNamespace())

    assert isinstance(response, InterfaceResponse)
    assert response.exit_code == 0
    assert response.data["valid"] is True
    expected_names = _mcp_tool_names()
    assert response.data["tool_count"] == len(expected_names)
    assert response.data["tools"] == expected_names
    assert "problems" not in response.data


@pytest.mark.asyncio
async def test_validate_flags_non_object_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool whose input schema is not an object schema is reported as a problem."""
    bad_tool = mcp_types.Tool(
        name="bad_tool",
        description="a tool with a non-object input schema",
        inputSchema={"type": "string"},
    )
    monkeypatch.setattr(catalog_server, "list_catalog_tools", lambda: [bad_tool])

    response = await catalog_server.handle_mcp_validate(SimpleNamespace())

    assert response.exit_code == 1
    assert response.data["valid"] is False
    assert response.data["tool_count"] == 1
    assert response.data["problems"]
    assert "bad_tool" in response.data["problems"][0]


@pytest.mark.asyncio
async def test_validate_flags_object_schema_without_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An object schema whose properties are not a mapping is reported as a problem."""
    bad_tool = mcp_types.Tool(
        name="propless_tool",
        description="an object schema with a non-object properties value",
        inputSchema={"type": "object", "properties": []},
    )
    monkeypatch.setattr(catalog_server, "list_catalog_tools", lambda: [bad_tool])

    response = await catalog_server.handle_mcp_validate(SimpleNamespace())

    assert response.exit_code == 1
    assert response.data["valid"] is False
    assert "no properties object" in response.data["problems"][0]


# --------------------------------------------------------------------------- #
# handle_mcp_serve                                                            #
# --------------------------------------------------------------------------- #


def _fake_application(*, initialize_result: bool = True) -> type:
    """A stand-in Application whose initialize is awaitable and container is a sentinel."""

    class FakeApplication:
        def __init__(self) -> None:
            self._container = sentinel.container

        async def initialize(self) -> bool:
            return initialize_result

        def _ensure_container(self) -> None:  # noqa: D401 — mirrors the real seam
            return None

    return FakeApplication


@pytest.fixture
def patched_serve(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the transport runners, telemetry flush, and Application for serve tests."""
    import orb.bootstrap as bootstrap

    run_stdio = AsyncMock()
    run_streamable_http = Mock()
    flush = Mock()

    monkeypatch.setattr(catalog_server, "run_stdio", run_stdio)
    monkeypatch.setattr(catalog_server, "run_streamable_http", run_streamable_http)
    monkeypatch.setattr(catalog_server, "_flush_telemetry", flush)
    monkeypatch.setattr(bootstrap, "Application", _fake_application())

    return {
        "run_stdio": run_stdio,
        "run_streamable_http": run_streamable_http,
        "flush": flush,
        "bootstrap": bootstrap,
    }


@pytest.mark.asyncio
async def test_serve_default_transport_runs_stdio(patched_serve: dict[str, Any]) -> None:
    """The default transport serves over stdio with the wired container."""
    result = await catalog_server.handle_mcp_serve(SimpleNamespace())

    patched_serve["run_stdio"].assert_awaited_once_with(sentinel.container)
    patched_serve["run_streamable_http"].assert_not_called()
    assert result == {"message": "MCP server stopped (stdio)"}
    patched_serve["flush"].assert_called_once_with()


@pytest.mark.asyncio
async def test_serve_http_transport_runs_streamable_http(patched_serve: dict[str, Any]) -> None:
    """transport=http dispatches to the Streamable HTTP runner with host/port/path."""
    args = SimpleNamespace(transport="http", host="0.0.0.0", port=9001, path="/rpc")

    result = await catalog_server.handle_mcp_serve(args)

    patched_serve["run_streamable_http"].assert_called_once_with(
        sentinel.container, "0.0.0.0", 9001, "/rpc", None
    )
    patched_serve["run_stdio"].assert_not_awaited()
    assert result == {"message": "MCP server stopped (0.0.0.0:9001/rpc)"}
    patched_serve["flush"].assert_called_once_with()


@pytest.mark.asyncio
async def test_serve_streamable_http_alias_normalizes_to_http(
    patched_serve: dict[str, Any],
) -> None:
    """The 'streamable-http' transport alias is normalized to the http path."""
    args = SimpleNamespace(transport="streamable-http")

    result = await catalog_server.handle_mcp_serve(args)

    patched_serve["run_streamable_http"].assert_called_once_with(
        sentinel.container, "127.0.0.1", 8000, "/mcp", None
    )
    assert result == {"message": "MCP server stopped (127.0.0.1:8000/mcp)"}


@pytest.mark.asyncio
async def test_serve_raises_when_initialization_fails(
    monkeypatch: pytest.MonkeyPatch, patched_serve: dict[str, Any]
) -> None:
    """A failed application initialize raises RuntimeError and never serves."""
    monkeypatch.setattr(
        patched_serve["bootstrap"], "Application", _fake_application(initialize_result=False)
    )

    with pytest.raises(RuntimeError, match="Failed to initialize"):
        await catalog_server.handle_mcp_serve(SimpleNamespace())

    patched_serve["run_stdio"].assert_not_awaited()
    patched_serve["run_streamable_http"].assert_not_called()
    # The initialize guard raises before the serve try/finally is entered, so no
    # telemetry flush runs — there is nothing yet to clean up.
    patched_serve["flush"].assert_not_called()


# --------------------------------------------------------------------------- #
# run_stdio                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_stdio_wires_streams_into_server_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_stdio hands the stdio read/write streams and init options to server.run."""
    import mcp.server.stdio as stdio_module

    server = MagicMock()
    server.run = AsyncMock()
    server.create_initialization_options.return_value = sentinel.init_options
    monkeypatch.setattr(catalog_server, "build_server", Mock(return_value=server))

    class FakeStdioServer:
        async def __aenter__(self) -> tuple[Any, Any]:
            return sentinel.read_stream, sentinel.write_stream

        async def __aexit__(self, *exc: Any) -> bool:
            return False

    monkeypatch.setattr(stdio_module, "stdio_server", Mock(return_value=FakeStdioServer()))

    await catalog_server.run_stdio(sentinel.container)

    server.run.assert_awaited_once_with(
        sentinel.read_stream, sentinel.write_stream, sentinel.init_options
    )


# --------------------------------------------------------------------------- #
# run_streamable_http                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_streamable_http_serves_starlette_app_under_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_streamable_http mounts a Starlette app and runs it under uvicorn (no port bind).

    Also drives the app's lifespan and mounted ASGI handler so the session
    manager's run/handle_request wiring is exercised without a real request.
    """
    import contextlib

    import uvicorn
    from mcp.server import streamable_http_manager
    from starlette.applications import Starlette

    session_manager = MagicMock()
    session_manager.handle_request = AsyncMock()

    @contextlib.asynccontextmanager
    async def _run() -> Any:
        yield

    session_manager.run = Mock(side_effect=_run)
    monkeypatch.setattr(
        streamable_http_manager,
        "StreamableHTTPSessionManager",
        Mock(return_value=session_manager),
    )

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn,
        "run",
        Mock(side_effect=lambda app, **kwargs: captured.update(app=app, kwargs=kwargs)),
    )

    catalog_server.run_streamable_http(sentinel.container, host="0.0.0.0", port=9002, path="/mcp")

    app = captured["app"]
    assert isinstance(app, Starlette)
    assert captured["kwargs"]["host"] == "0.0.0.0"
    assert captured["kwargs"]["port"] == 9002

    # Drive the lifespan so session_manager.run() is entered and exited.
    async with app.router.lifespan_context(app):
        session_manager.run.assert_called_once_with()

    # Drive the mounted ASGI handler so it delegates to handle_request.
    mount = app.routes[0]
    await mount.app(sentinel.scope, sentinel.receive, sentinel.send)
    session_manager.handle_request.assert_awaited_once_with(
        sentinel.scope, sentinel.receive, sentinel.send
    )


def _patched_session_manager(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Patch StreamableHTTPSessionManager so handle_request answers a plain 200.

    Lets a request that clears authentication reach the mount without a real
    MCP session handshake.
    """
    import contextlib

    from mcp.server import streamable_http_manager

    async def _answer_ok(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    session_manager = MagicMock()
    session_manager.handle_request = AsyncMock(side_effect=_answer_ok)

    @contextlib.asynccontextmanager
    async def _run() -> Any:
        yield

    session_manager.run = Mock(side_effect=_run)
    monkeypatch.setattr(
        streamable_http_manager,
        "StreamableHTTPSessionManager",
        Mock(return_value=session_manager),
    )
    return session_manager


def _server_config(*, auth_enabled: bool, secret: str = "x" * 32) -> Any:
    """A minimal ServerConfig with auth enabled/disabled via the bearer_token strategy."""
    from pydantic import SecretStr

    from orb.config.schemas.server_schema import AuthConfig, BearerTokenAuthSubConfig, ServerConfig

    if auth_enabled:
        auth = AuthConfig(
            enabled=True,
            strategy="bearer_token",
            bearer_token=BearerTokenAuthSubConfig(secret_key=SecretStr(secret)),
        )
    else:
        auth = AuthConfig(enabled=False)
    return ServerConfig(auth=auth)


@pytest.mark.asyncio
async def test_run_streamable_http_adds_auth_middleware_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled auth wires AuthMiddleware into the Streamable HTTP app."""
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware

    _patched_session_manager(monkeypatch)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn, "run", Mock(side_effect=lambda app, **kwargs: captured.update(app=app))
    )

    catalog_server.run_streamable_http(
        sentinel.container,
        host="127.0.0.1",
        port=9003,
        path="/mcp",
        server_config=_server_config(auth_enabled=True),
    )

    middleware_classes = [m.cls for m in captured["app"].user_middleware]
    assert BaseHTTPMiddleware in middleware_classes or any(
        issubclass(cls, BaseHTTPMiddleware) for cls in middleware_classes
    )


@pytest.mark.asyncio
async def test_run_streamable_http_omits_auth_middleware_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled (or absent) auth config leaves the app without AuthMiddleware."""
    import uvicorn

    _patched_session_manager(monkeypatch)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn, "run", Mock(side_effect=lambda app, **kwargs: captured.update(app=app))
    )

    catalog_server.run_streamable_http(sentinel.container, host="127.0.0.1", port=9004, path="/mcp")

    assert captured["app"].user_middleware == []


def test_run_streamable_http_warns_on_non_loopback_bind_with_auth_disabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Binding non-loopback with auth disabled logs a security warning, like REST."""
    import logging

    import uvicorn

    _patched_session_manager(monkeypatch)
    monkeypatch.setattr(uvicorn, "run", Mock())

    with caplog.at_level(logging.WARNING, logger="orb.interface.mcp.catalog_server"):
        catalog_server.run_streamable_http(sentinel.container, host="0.0.0.0", port=9005)

    assert any("SECURITY WARNING" in record.message for record in caplog.records)


def test_run_streamable_http_rejects_unauthenticated_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request with no credentials is rejected before it reaches the MCP session."""
    import uvicorn
    from starlette.testclient import TestClient

    session_manager = _patched_session_manager(monkeypatch)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn, "run", Mock(side_effect=lambda app, **kwargs: captured.update(app=app))
    )

    catalog_server.run_streamable_http(
        sentinel.container,
        host="127.0.0.1",
        port=9006,
        path="/mcp",
        server_config=_server_config(auth_enabled=True),
    )

    with TestClient(captured["app"]) as client:
        response = client.post("/mcp", json={})

    assert response.status_code == 401
    session_manager.handle_request.assert_not_called()


def test_run_streamable_http_allows_authenticated_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request with a valid bearer token reaches the MCP session manager."""
    import jwt
    import uvicorn
    from starlette.testclient import TestClient

    secret = "x" * 32
    session_manager = _patched_session_manager(monkeypatch)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn, "run", Mock(side_effect=lambda app, **kwargs: captured.update(app=app))
    )

    catalog_server.run_streamable_http(
        sentinel.container,
        host="127.0.0.1",
        port=9007,
        path="/mcp",
        server_config=_server_config(auth_enabled=True, secret=secret),
    )

    token = jwt.encode({"sub": "operator-user", "roles": ["operator"]}, secret, algorithm="HS256")
    with TestClient(captured["app"]) as client:
        response = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    session_manager.handle_request.assert_called_once()


# --------------------------------------------------------------------------- #
# _flush_telemetry                                                            #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Real StreamableHTTPSessionManager round trip (no mocked transport)         #
# --------------------------------------------------------------------------- #


def _request_machines_container() -> tuple[MagicMock, AsyncMock]:
    """A container that resolves request_machines' orchestrator through a real formatter.

    Returns the container and the orchestrator mock so a test can assert
    whether the orchestrator was actually invoked.
    """
    from orb.application.services.orchestration.acquire_machines import (
        AcquireMachinesOrchestrator,
    )
    from orb.application.services.orchestration.dtos import AcquireMachinesOutput
    from orb.infrastructure.di.container import DIContainer
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
    from orb.interface.response_formatting_service import ResponseFormattingService

    orchestrator = AsyncMock(spec=AcquireMachinesOrchestrator)
    orchestrator.execute.return_value = AcquireMachinesOutput(
        request_id="req-1", status="pending", machine_ids=[]
    )
    formatter = ResponseFormattingService(DefaultSchedulerStrategy(logger=MagicMock()))

    container = MagicMock(spec=DIContainer)
    container.get.side_effect = lambda cls: {
        AcquireMachinesOrchestrator: orchestrator,
        ResponseFormattingService: formatter,
    }.get(cls)
    return container, orchestrator


async def _call_tool_over_real_transport(
    app: Any, token: str, tool_name: str, arguments: dict[str, Any]
) -> Any:
    """Drive a real initialize + tools/call exchange through the mounted app.

    The caller must already have the Starlette app's lifespan entered (the
    real ``StreamableHTTPSessionManager`` instance only supports being run
    once). The MCP client's ``streamable_http_client``/``ClientSession`` then
    carries the actual wire protocol over an in-process ASGI transport — no
    transport-layer mock — so the request reaches ``AuthMiddleware`` and the
    session manager exactly as a real network client's would.
    """
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    http_client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    )
    async with http_client:
        async with streamable_http_client("http://testserver/mcp/", http_client=http_client) as (
            read_stream,
            write_stream,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.call_tool(tool_name, arguments)


@pytest.mark.asyncio
async def test_real_streamable_http_session_enforces_bearer_auth_and_tool_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real session-manager round trip enforces both auth and per-tool roles.

    Unlike the transport-wiring tests above, this drives the actual
    ``StreamableHTTPSessionManager`` through an in-process ASGI transport
    with no mocked ``handle_request``: an unauthenticated POST is rejected
    with 401 before the session manager is reached, an authenticated viewer
    is refused by role enforcement on an operator-only tool, and an
    authenticated operator reaches the orchestrator (mocked only at the DI
    boundary).
    """
    import httpx
    import jwt
    import uvicorn

    container, orchestrator = _request_machines_container()
    secret = "r" * 32
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn, "run", Mock(side_effect=lambda app, **kwargs: captured.update(app=app))
    )

    catalog_server.run_streamable_http(
        container,
        host="127.0.0.1",
        port=9100,
        path="/mcp",
        server_config=_server_config(auth_enabled=True, secret=secret),
    )
    app = captured["app"]

    # The real StreamableHTTPSessionManager instance only supports being run
    # once, so every interaction below shares a single lifespan entry.
    async with app.router.lifespan_context(app):
        # No credentials: AuthMiddleware rejects before the session manager runs.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post("/mcp", json={})
        assert response.status_code == 401

        arguments = {"template_id": "t1", "requested_count": 1}

        # Authenticated viewer: the session reaches call_tool but role
        # enforcement denies it; the orchestrator is never invoked.
        viewer_token = jwt.encode(
            {"sub": "viewer-user", "roles": ["viewer"]}, secret, algorithm="HS256"
        )
        denied = await _call_tool_over_real_transport(
            app, viewer_token, "request_machines", arguments
        )
        assert denied.is_error is True
        assert "insufficient permissions" in denied.content[0].text
        orchestrator.execute.assert_not_called()

        # Authenticated operator: role enforcement permits the call and the
        # orchestrator executes.
        operator_token = jwt.encode(
            {"sub": "operator-user", "roles": ["operator"]}, secret, algorithm="HS256"
        )
        allowed = await _call_tool_over_real_transport(
            app, operator_token, "request_machines", arguments
        )
        assert allowed.is_error is False
        orchestrator.execute.assert_called_once()


# --------------------------------------------------------------------------- #
# Real stdio-equivalent session round trip (no mocked transport)             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_real_stdio_session_lists_and_calls_tools() -> None:
    """A real ClientSession round-trips tools/list and tools/call over the server's
    own ``server.run()`` loop, connected through in-process memory streams rather
    than real OS pipes — the same read/write-stream contract stdio hands it, with
    no caller identity attached, so role enforcement stays bypassed as it does
    for a real stdio client.
    """
    import anyio
    import mcp.shared.memory as mcp_memory
    from mcp import ClientSession

    from orb.application.services.orchestration.dtos import ListMachinesOutput
    from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
    from orb.infrastructure.di.container import DIContainer
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
    from orb.interface.response_formatting_service import ResponseFormattingService

    orchestrator = AsyncMock(spec=ListMachinesOrchestrator)
    orchestrator.execute.return_value = ListMachinesOutput(
        machines=[], count=0, next_cursor=None, total_count=0
    )
    formatter = ResponseFormattingService(DefaultSchedulerStrategy(logger=MagicMock()))
    container = MagicMock(spec=DIContainer)
    container.get.side_effect = lambda cls: {
        ListMachinesOrchestrator: orchestrator,
        ResponseFormattingService: formatter,
    }.get(cls)

    server = catalog_server.build_server(container)

    async with mcp_memory.create_client_server_memory_streams() as (
        client_streams,
        server_streams,
    ):
        client_read, client_write = client_streams
        server_read, server_write = server_streams

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                server.run, server_read, server_write, server.create_initialization_options()
            )

            async with ClientSession(client_read, client_write) as session:
                await session.initialize()

                listed = await session.list_tools()
                assert any(tool.name == "list_machines" for tool in listed.tools)

                result = await session.call_tool("list_machines", {})
                assert result.is_error is False
                orchestrator.execute.assert_called_once()


def test_flush_telemetry_invokes_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """_flush_telemetry calls the telemetry shutdown hook."""
    import orb.bootstrap.telemetry as telemetry

    shutdown = Mock()
    monkeypatch.setattr(telemetry, "shutdown_telemetry", shutdown)

    catalog_server._flush_telemetry()

    shutdown.assert_called_once_with()


def test_flush_telemetry_swallows_shutdown_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure in the telemetry shutdown hook must not propagate out of cleanup."""
    import orb.bootstrap.telemetry as telemetry

    monkeypatch.setattr(
        telemetry, "shutdown_telemetry", Mock(side_effect=RuntimeError("otel down"))
    )

    # Must not raise.
    catalog_server._flush_telemetry()

"""Add the ``mcp`` command and its actions."""

from __future__ import annotations

from orb.cli.parsers.parent_parsers import ParentParsers


def add_mcp_actions(subparsers, pp: ParentParsers, resource_parsers: dict) -> None:
    """Add the ``mcp`` command and its actions."""
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Operate the MCP (Model Context Protocol) interface",
        description="Operate the MCP (Model Context Protocol) interface — manage tools, validate, and serve.",
    )
    resource_parsers["mcp"] = mcp_parser
    mcp_subparsers = mcp_parser.add_subparsers(
        dest="action", metavar="<action>", help="MCP actions", required=True
    )

    mcp_subparsers.add_parser(
        "validate",
        help="Validate the MCP tool set offline (no server)",
        description="Validate the MCP tool set offline — build every catalog-derived "
        "tool and confirm each has a resolvable input schema, without starting a server.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    mcp_serve = mcp_subparsers.add_parser(
        "serve",
        help="Start MCP server",
        description="Start the MCP server to expose ORB tools to MCP clients.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    mcp_serve.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http"],
        default="stdio",
        help="Transport to serve over (default: stdio; 'streamable-http' aliases 'http')",
    )
    mcp_serve.add_argument(
        "--host", default="127.0.0.1", help="Host to bind for http transport (default: 127.0.0.1)"
    )
    mcp_serve.add_argument(
        "--port", type=int, default=8000, help="Port to bind for http transport (default: 8000)"
    )
    mcp_serve.add_argument(
        "--path", default="/mcp", help="URL path to mount for http transport (default: /mcp)"
    )

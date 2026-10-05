# MCP Integration Guide

This guide explains how to integrate the Open Resource Broker with AI assistants using the Model Context Protocol (MCP).

## Overview

The broker ships a catalog-driven MCP server. Every operation the broker
declares once in its operation catalog and marks as exposed on the MCP
interface becomes an MCP tool automatically: the tool name is the catalog key,
its input schema is derived from the operation's input type, and its result is
rendered through the same formatting seam the CLI, REST, and SDK use. The tool
set therefore never drifts from the catalog.

An AI assistant connected to the server can:

1. Discover the available infrastructure operations (`tools/list`)
2. Execute an operation (`tools/call`)
3. Receive the operation's canonical JSON body as the tool result

## Starting the server

The server is launched through the CLI:

```bash
# Serve over stdio (the default transport, recommended for local AI assistants)
orb mcp serve

# Serve over Streamable HTTP for networked clients
orb mcp serve --transport http --host 127.0.0.1 --port 8000 --path /mcp
```

### `orb mcp serve` flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--transport {stdio,http,streamable-http}` | `stdio` | Transport to serve over. `streamable-http` is an alias for `http`. |
| `--host HOST` | `127.0.0.1` | Host to bind (http transport only). |
| `--port PORT` | `8000` | Port to bind (http transport only). |
| `--path PATH` | `/mcp` | URL path to mount (http transport only). |

When the transport is `stdio`, the `--host`/`--port`/`--path` flags are ignored;
the client speaks JSON-RPC over the process's standard input and output.

### Authentication for the Streamable HTTP transport

The Streamable HTTP transport reuses the REST API's `server.auth` configuration.
When `server.auth.enabled` is true, every request to the mounted path is
authenticated with the configured strategy (bearer token, IAM, Cognito, and so
on) before it reaches a tool, and each tool enforces the same minimum role its
REST equivalent requires — `request_machines`, `return_machines`,
`cancel_request`, `stop_machines`, and `start_machines` require at least the
`operator` role; the remaining tools require `viewer`.

When `server.auth.enabled` is false, every caller is treated as an anonymous
viewer, so the mutating tools listed above are refused for everyone. Bind to
`127.0.0.1` (the default) unless authentication is enabled; binding to a
non-loopback host with authentication disabled logs a warning. The stdio
transport has no network boundary and is not affected by this configuration.

The REST API also accepts a daemon-issued loopback-admin token regardless of
`server.auth.enabled`, so the CLI's reload command and local tooling can
authenticate as admin without a configured auth strategy. The Streamable HTTP
transport does not implement this bypass: its authentication is governed
entirely by `server.auth.enabled` and the configured strategy, with no
loopback-admin exception. A caller reaching the MCP transport from the same
host as the broker gets no special treatment.

### Validating the tool set offline

`orb mcp validate` builds the tool set straight from the catalog — no server or
client is spun up — and checks that every MCP-exposed operation yields a valid
object input schema. It prints the tool count and names, exiting non-zero if any
operation fails to resolve, so it is safe to run in CI.

```bash
orb mcp validate
```

## Available MCP tools

Tools are derived from the catalog at runtime; run `orb mcp validate` (or
`tools/list` from a connected client) for the authoritative set. The operations
typically exposed include:

### Template operations

- `list_templates`: List available compute templates
- `get_template`: Get a specific template's details
- `validate_template`: Validate a template configuration

### Request and machine operations

- `request_machines`: Request new compute instances (`requested_count` selects how many)
- `get_request_status`: Check the status of one or more requests (`request_ids`)
- `list_requests`: List provisioning requests
- `list_machines`: List provisioned machines
- `return_machines`: Return compute instances (`machine_ids`)
- `list_return_requests`: List machine return requests
- `start_machines` / `stop_machines`: Start or stop machines

### Provider operations

- `list_providers`: List configured providers
- `get_provider_health`: Check provider health
- `get_provider_config`: Get provider configuration
- `get_provider_metrics`: Get provider performance metrics

## Tool results

Every tool result is a single text content block whose text is the operation's
canonical JSON body — the same body the CLI would emit for that operation. A
failed call (unknown tool, or an operation that raises) is returned as a tool
result with `isError` set, not as a protocol-level exception, so a conformant
client can surface the failure to the user.

## AI assistant integration examples

### Claude Desktop configuration

```json
{
  "mcpServers": {
    "open-resource-broker": {
      "command": "orb",
      "args": ["mcp", "serve"]
    }
  }
}
```

### Python MCP client

```python
import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

server_params = StdioServerParameters(command="orb", args=["mcp", "serve"])


async def main():
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])

            result = await session.call_tool("list_templates", {"active_only": True})
            body = json.loads(result.content[0].text)
            print("templates:", body.get("templates"))


asyncio.run(main())
```

## Complete examples

For working implementations, see:

- **Python client**: [examples/mcp/python/client_example.py](#python-client-example)
- **Node.js client**: [examples/mcp/nodejs/client_example.js](#nodejs-client-example)

These examples include error handling, async context management, and multiple
tool-usage patterns over the stdio transport.

## Troubleshooting

### Common issues

1. **Tool not found**: The tool name must match a catalog key exposed on the MCP interface. Run `orb mcp validate` to list the current tool set.
2. **Invalid arguments**: Check the tool's `inputSchema` from `tools/list`; required fields are the input type's fields without defaults.
3. **Connection issues (http)**: Verify the server is reachable at `http://<host>:<port><path>`.

### Debugging

```bash
# Verbose server logging
orb mcp serve --verbose

# List the tool set without starting a server
orb mcp validate
```

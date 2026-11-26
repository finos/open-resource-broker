# MCP Tools

The broker exposes its operations to AI assistants as MCP tools through a
single catalog-driven server. There is no separate "tools mode" library and no
in-process tools object: the server started by `orb mcp serve` is the one and
only MCP surface, and its tool set is derived from the operation catalog.

## How tools are derived

Every operation the broker declares in its operation catalog and marks as
exposed on the MCP interface becomes a tool automatically:

- the **tool name** is the catalog key (for example `list_templates`);
- the **input schema** is generated from the operation's input type — each
  field becomes a JSON-Schema property, and a field without a default is marked
  required;
- the **result** is the operation output rendered through the shared response
  formatting seam, so a tool's body stays in lockstep with the CLI, REST, and
  SDK bodies for the same operation.

Because the tool set is generated from the catalog, adding an MCP-exposed
operation to the catalog adds a tool with no further wiring, and the tool set
can never drift from the operations the broker actually supports.

## Listing and validating tools

Use `orb mcp validate` to build the tool set offline (no server or client is
started) and confirm every MCP-exposed operation yields a valid object input
schema. It prints the tool count and names and exits non-zero on any problem,
so it is safe to run in CI:

```bash
orb mcp validate
```

From a connected client, the standard `tools/list` request returns the same set
with each tool's `inputSchema`.

## Calling a tool

Tools are invoked with the standard MCP `tools/call` request. The result is a
single text content block whose text is the operation's canonical JSON body.

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

            result = await session.call_tool("list_templates", {"active_only": True})
            body = json.loads(result.content[0].text)
            print(f"Found {len(body.get('templates', []))} templates")


asyncio.run(main())
```

## Tool schema format

Each tool carries a JSON-Schema `inputSchema` describing its arguments:

```json
{
  "name": "list_templates",
  "description": "list_templates — dispatched through ListTemplatesOrchestrator. Returns the operation result rendered as the broker's canonical body.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "active_only": { "type": "boolean" },
      "provider_api": { "type": "string" }
    },
    "additionalProperties": true
  }
}
```

Extra keys a caller passes that do not name an input field are ignored, so
clients can send additional properties safely.

## Result format

A successful call returns the operation's JSON body as text content:

```json
{
  "templates": [
    {
      "templateId": "RunInstances-OnDemand",
      "name": "Run Instances On-Demand",
      "providerType": "aws"
    }
  ],
  "total_count": 1
}
```

A failed call — an unknown tool name, or an operation that raises — is returned
as a tool result with `isError` set and the failure message in the text block,
rather than as a protocol-level exception. A conformant client checks `isError`
before parsing the body.

## Selected tools

The tool set is authoritative from `orb mcp validate`. Commonly exposed
operations:

### Template operations
- `list_templates` — list available templates
- `get_template` — get a specific template
- `validate_template` — validate a template configuration

### Request and machine operations
- `request_machines` — request instances (`requested_count`)
- `get_request_status` — status of one or more requests (`request_ids`)
- `list_requests` — list provisioning requests; `start_time` and `end_time` (ISO-8601) limit them to a creation-time range
- `list_machines` — list provisioned machines
- `return_machines` — return instances (`machine_ids`)
- `list_return_requests` — list return requests
- `start_machines` / `stop_machines` — start or stop machines

### Provider operations
- `list_providers` — list configured providers
- `get_provider_health` — provider health
- `get_provider_config` — provider configuration
- `get_provider_metrics` — provider performance metrics

## Next steps

- [MCP Integration Guide](#integration-guide) — transports, client examples, and assistant configuration
- [CLI Reference](#cli-reference) — complete CLI command reference

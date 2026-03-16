# End-to-End Workflow Guide

This guide walks through the complete machine lifecycle — from initialization through provisioning to return — across all four ORB interfaces: CLI, Python SDK, REST API, and MCP.

## Lifecycle Overview

| Stage | Description | Terminal? |
|-------|-------------|-----------|
| Initialize | Configure ORB and discover infrastructure | — |
| List templates | Browse available compute templates | — |
| Create request | Submit a provisioning request | — |
| Poll status | Wait for request to reach a terminal state | — |
| Extract machines | Read machine IDs from the completed request | — |
| Return machines | Submit a return request | — |
| Poll return | Wait for return to complete | — |

## Request Status Reference

| Status | Terminal | Description |
|--------|----------|-------------|
| `pending` | No | Request received, not yet processed |
| `in_progress` | No | Machines are being provisioned |
| `complete` | Yes | All machines provisioned successfully |
| `partial` | Yes | Some machines provisioned |
| `failed` | Yes | Provisioning failed |
| `cancelled` | Yes | Request was cancelled |

---

## Configuration

### orb init

`orb init` writes `config.json` and `default_config.json` into the ORB config directory. The config directory location depends on how ORB is installed:

| Install type | Config directory |
|---|---|
| uv tool install | `~/.orb/config/` |
| mise-managed Python | `~/.orb/config/` |
| User install (`pip install --user`) | `~/.orb/config/` |
| Standard virtualenv | `<venv-parent>/config/` |
| System install (`/usr` or `/opt`) | `<sys.prefix>/orb/config/` |
| Development (pyproject.toml found) | `<project-root>/config/` |
| `ORB_CONFIG_DIR` env var | value of `ORB_CONFIG_DIR` |
| `ORB_ROOT_DIR` env var | `$ORB_ROOT_DIR/config/` |

Run `orb config show` to see the exact path in use.

**Interactive wizard** (recommended for first-time setup):

```bash
orb init
```

**Non-interactive** (for scripted or CI environments):

```bash
# Basic — provider discovers infrastructure automatically
orb init --non-interactive --provider aws

# Reinitialize over an existing config
orb init --force
```

Non-interactive flags available:

| Flag | Description |
|---|---|
| `--non-interactive` | Skip the interactive wizard |
| `--provider <type>` | Provider type (e.g. `aws`) |
| `--scheduler <type>` | Scheduler type (`default` or `hostfactory`) |
| `--config-dir <path>` | Custom config directory |
| `--force` | Overwrite existing config |

> **AWS-specific non-interactive flags:** `--region`, `--profile`, `--subnet-ids`, `--security-group-ids`, `--fleet-role` are AWS-only. For other providers, use the interactive wizard (`orb init`) which adapts to the selected provider's requirements.

The resulting config file looks like:

```json
{
  "scheduler": {"type": "default"},
  "provider": {
    "providers": [
      {
        "name": "aws_myprofile_us-east-1",
        "type": "aws",
        "enabled": true,
        "config": {"profile": "myprofile", "region": "us-east-1"},
        "default": true,
        "template_defaults": {
          "subnet_ids": ["subnet-abc", "subnet-def"],
          "security_group_ids": ["sg-123"]
        }
      }
    ]
  }
}
```

**Inspect and edit config after init:**

```bash
orb config show                        # print current config
orb config get provider                # get a single key
orb config set scheduler.type default  # set a single key
```

### SDK config modes

The SDK supports four initialization modes:

```python
from orb import ORBClient as orb

# Mode 1 — default: reads env vars (ORB_CONFIG_FILE, ORB_PROVIDER, ORB_REGION, …)
#           or built-in defaults
async with orb() as sdk: ...

# Mode 2 — SDK config dict: tune provider, region, timeout, log_level, etc.
async with orb(config={"provider": "aws", "region": "us-west-2"}) as sdk: ...

# Mode 3 — config file on disk
async with orb(config_path="/etc/orb/config.json") as sdk: ...

# Mode 4 — full app config in memory (Lambda, CI, notebooks — no filesystem)
app_cfg = {
    "scheduler": {"type": "default"},
    "provider": {
        "providers": [
            {"name": "default", "type": "aws", "enabled": True,
             "config": {"region": "us-east-1"}, "default": True}
        ]
    }
}
async with orb(app_config=app_cfg) as sdk: ...
```

### REST API and MCP

Neither the REST API nor the MCP server exposes config endpoints. Configuration must be set via `orb init` or a config file before starting the server.

---

## Template Management

### CLI

```bash
# List all templates (optionally filter by provider API)
orb templates list
orb templates list --provider-api EC2Fleet

# Inspect a single template
orb templates show my-tmpl

# Create from a JSON file
orb templates create --file template.json
orb templates create --file template.json --validate-only  # dry-run validation

# Update fields from a JSON file (only keys present in the file are changed)
orb templates update my-tmpl --file changes.json

# Delete (--force skips confirmation prompt)
orb templates delete my-tmpl
orb templates delete my-tmpl --force

# Validate an existing template or a file
orb templates validate my-tmpl
orb templates validate --file template.json

# Generate a starter template scaffold
orb templates generate
orb templates generate --provider-api EC2Fleet
orb templates generate --provider-specific   # include provider-specific fields
orb templates generate --generic             # minimal provider-agnostic scaffold

# Refresh the template cache
orb templates refresh
```

### SDK

All template methods are auto-discovered via CQRS.

```python
async with orb() as sdk:
    # List templates
    result = await sdk.list_templates(active_only=True)
    # -> list of template dicts

    # Get a single template
    tmpl = await sdk.get_template(template_id="my-tmpl")
    # -> template dict

    # Create a template (template_id, provider_api, image_id are required)
    result = await sdk.create_template(
        template_id="my-tmpl",
        provider_api="EC2Fleet",
        image_id="ami-0abcdef1234567890",
        name="My Template",
        instance_type="t3.medium",
        subnet_ids=["subnet-abc"],
        security_group_ids=["sg-123"],
        tags={"env": "dev"},
    )
    # -> {"created": True}
    # -> {"created": False, "validation_errors": ["..."]}  on failure

    # Validate a template
    result = await sdk.validate_template(template_id="my-tmpl")
    # -> {"valid": True, "validation_errors": []}

    # Update fields (only supplied kwargs are changed)
    result = await sdk.update_template(
        template_id="my-tmpl",
        name="Updated Name",
        instance_type="t3.large",
    )
    # -> {"updated": True}
    # -> {"updated": False, "validation_errors": ["..."]}  on failure

    # Delete a template
    result = await sdk.delete_template(template_id="my-tmpl")
    # -> {"deleted": True}

    # Refresh the template cache
    await sdk.refresh_templates()
```

### REST API

```bash
# List templates
curl -s http://localhost:8000/api/v1/templates/ | jq .

# Filter by provider API
curl -s "http://localhost:8000/api/v1/templates/?provider_api=EC2Fleet" | jq .

# Get a single template
curl -s http://localhost:8000/api/v1/templates/my-tmpl | jq .

# Create a template
curl -s -X POST http://localhost:8000/api/v1/templates/ \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": "my-tmpl",
    "provider_api": "EC2Fleet",
    "image_id": "ami-0abcdef1234567890",
    "name": "My Template",
    "instance_type": "t3.medium",
    "subnet_ids": ["subnet-abc"],
    "security_group_ids": ["sg-123"],
    "tags": {"env": "dev"}
  }'

# Update a template (only supplied fields are changed)
curl -s -X PUT http://localhost:8000/api/v1/templates/my-tmpl \
  -H "Content-Type: application/json" \
  -d '{"name": "Updated Name", "instance_type": "t3.large"}'

# Delete a template
curl -s -X DELETE http://localhost:8000/api/v1/templates/my-tmpl

# Validate a template config
curl -s -X POST http://localhost:8000/api/v1/templates/validate \
  -H "Content-Type: application/json" \
  -d '{"template_id": "my-tmpl", "provider_api": "EC2Fleet", "image_id": "ami-0abcdef1234567890"}'

# Refresh the template cache
curl -s -X POST http://localhost:8000/api/v1/templates/refresh
```

### MCP

The MCP server exposes read-only template tools only. There are no create, update, or delete tools.

```json
{"name": "list_templates", "arguments": {}}
```

```json
{"name": "get_template", "arguments": {"template_id": "my-tmpl"}}
```

```json
{"name": "validate_template", "arguments": {"template_id": "my-tmpl"}}
```

To create, update, or delete templates from an MCP-driven workflow, use the REST API or CLI directly.

---

## CLI

### Full workflow script

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. Initialize ORB (first-time setup — discovers AWS infrastructure and writes config)
orb init

# 2. Generate example templates for your provider
orb templates generate

# 3. List available templates and pick one
orb templates list

TEMPLATE_ID="aws-basic"   # replace with a template ID from the list above
COUNT=3

# 4. Request machines
orb machines request "$TEMPLATE_ID" "$COUNT"
# Output includes a request ID, e.g.: req-abc123

REQUEST_ID="req-abc123"   # replace with the request ID from the output above

# 5a. Manual poll loop — check status until terminal
while true; do
  STATUS=$(orb requests status "$REQUEST_ID" --format json | jq -r '.status')
  echo "Status: $STATUS"
  case "$STATUS" in
    complete|partial|failed|cancelled)
      break
      ;;
  esac
  sleep 15
done

# 5b. --wait shortcut (equivalent to the loop above)
orb machines request "$TEMPLATE_ID" "$COUNT" --wait --timeout 600

# 6. Extract machine IDs from the completed request
MACHINE_IDS=$(orb requests status "$REQUEST_ID" --format json \
  | jq -r '.machines[].machine_id')
echo "Machines: $MACHINE_IDS"

# 7. Return machines when done
# shellcheck disable=SC2086
orb machines return $MACHINE_IDS

# 8. Poll return status
RETURN_ID="ret-xyz789"    # replace with the return request ID from step 7 output

while true; do
  STATUS=$(orb requests status "$RETURN_ID" --format json | jq -r '.status')
  echo "Return status: $STATUS"
  case "$STATUS" in
    complete|failed|cancelled)
      break
      ;;
  esac
  sleep 15
done
```

---

## Python SDK

### Full lifecycle example

```python
import asyncio
from orb import ORBClient as orb


async def full_lifecycle() -> None:
    # ORBClient as a context manager handles initialize() and cleanup() automatically.
    async with orb() as sdk:

        # 1. List available templates
        result = await sdk.list_templates(active_only=True)
        templates = result if isinstance(result, list) else result.get("templates", [])
        print(f"Found {len(templates)} template(s)")

        template_id = templates[0].get("template_id") or templates[0].get("id")

        # 2. Create a provisioning request
        request_result = await sdk.create_request(template_id=template_id, count=3)
        request_id = (
            request_result.get("created_request_id")
            or request_result.get("request_id")
            or request_result.get("id")
        )
        print(f"Request created: {request_id}")

        # 3. Wait for the request to reach a terminal status.
        #    wait_for_request polls every poll_interval seconds until timeout.
        final = await sdk.wait_for_request(
            request_id,
            timeout=600.0,
            poll_interval=15.0,
        )
        print(f"Request status: {final.get('status')}")

        # 4. Extract machine IDs from the completed request
        machines = final.get("machines", [])
        machine_ids = [m.get("machine_id") or m.get("id") for m in machines]
        print(f"Machines: {machine_ids}")

        # 5. Return machines when done
        return_result = await sdk.create_return_request(machine_ids=machine_ids)
        return_request_id = (
            return_result.get("created_request_id")
            or return_result.get("request_id")
            or return_result.get("id")
        )
        print(f"Return request created: {return_request_id}")

        # 6. Wait for the return to complete
        return_final = await sdk.wait_for_return(
            return_request_id,
            timeout=300.0,
            poll_interval=10.0,
        )
        print(f"Return status: {return_final.get('status')}")


if __name__ == "__main__":
    asyncio.run(full_lifecycle())
```

### wait_for_request / wait_for_return signatures

```python
await sdk.wait_for_request(
    request_id: str,
    *,
    timeout: float = 300.0,      # seconds before TimeoutError is raised
    poll_interval: float = 10.0, # seconds between status checks
) -> dict

await sdk.wait_for_return(
    return_request_id: str,
    *,
    timeout: float = 300.0,
    poll_interval: float = 10.0,
) -> dict
```

Both methods raise `TimeoutError` if the request does not reach a terminal status within `timeout` seconds.

---

## REST API

### Base URL

```
http://localhost:8000/api/v1
```

### Full workflow

```bash
# 1. List templates
curl -s http://localhost:8000/api/v1/templates | jq .

# 2. Request machines
curl -s -X POST http://localhost:8000/api/v1/machines/request \
  -H "Content-Type: application/json" \
  -d '{"template_id": "aws-basic", "count": 3}'
# Response includes request_id, e.g.: "req-abc123"

# 3. Poll request status until terminal
REQUEST_ID="req-abc123"

while true; do
  RESPONSE=$(curl -s "http://localhost:8000/api/v1/requests/${REQUEST_ID}")
  STATUS=$(echo "$RESPONSE" | jq -r '.status')
  echo "Status: $STATUS"
  case "$STATUS" in
    complete|partial|failed|cancelled)
      break
      ;;
  esac
  sleep 15
done

# 4. Extract machine IDs
MACHINE_IDS=$(curl -s "http://localhost:8000/api/v1/requests/${REQUEST_ID}" \
  | jq -r '.machines[].machine_id')

# 5. Return machines
curl -s -X POST http://localhost:8000/api/v1/machines/return \
  -H "Content-Type: application/json" \
  -d "{\"machine_ids\": $(echo "$MACHINE_IDS" | jq -R . | jq -s .)}"
# Response includes return request_id, e.g.: "ret-xyz789"

# 6. Poll return status
RETURN_ID="ret-xyz789"

while true; do
  STATUS=$(curl -s "http://localhost:8000/api/v1/requests/${RETURN_ID}" \
    | jq -r '.status')
  echo "Return status: $STATUS"
  case "$STATUS" in
    complete|failed|cancelled)
      break
      ;;
  esac
  sleep 10
done
```

### Response envelope

All REST responses use this envelope:

```json
{
  "success": true,
  "data": {},
  "error": null,
  "timestamp": "2025-07-09T07:00:00Z"
}
```

---

## MCP

The MCP server exposes ORB operations as tools callable by AI assistants. The tool sequence for the full lifecycle is:

### 1. List templates

```json
{
  "name": "list_templates",
  "arguments": {}
}
```

### 2. Create a request

```json
{
  "name": "request_machines",
  "arguments": {
    "template_id": "aws-basic",
    "count": 3
  }
}
```

### 3. Poll request status

Call `get_request_status` repeatedly until the status is terminal:

```json
{
  "name": "get_request_status",
  "arguments": {
    "request_id": "req-abc123"
  }
}
```

Repeat until `status` is one of: `complete`, `partial`, `failed`, `cancelled`.

### 4. Return machines

```json
{
  "name": "return_machines",
  "arguments": {
    "machine_ids": ["machine-1", "machine-2", "machine-3"]
  }
}
```

### 5. Poll return status

```json
{
  "name": "get_request_status",
  "arguments": {
    "request_id": "ret-xyz789"
  }
}
```

Repeat until `status` is `complete`, `failed`, or `cancelled`.

---

## Error Handling

### CLI

Non-zero exit codes indicate failure. Use `--format json` and check the `error` field for structured error output. Common issues:

- `orb init` not run — run `orb init` before any other command
- Invalid template ID — run `orb templates list` to see available templates
- Timeout on `--wait` — increase `--timeout` or poll manually

### SDK

```python
from orb.sdk.exceptions import ConfigurationError, MethodExecutionError, ProviderError, SDKError

try:
    async with orb() as sdk:
        result = await sdk.create_request(template_id="aws-basic", count=3)
except ConfigurationError as e:
    # ORB not initialized or config file missing
    print(f"Config error: {e}")
except ProviderError as e:
    # AWS provider unreachable or misconfigured
    print(f"Provider error: {e}")
except MethodExecutionError as e:
    # A specific SDK method failed (e.g. invalid template ID)
    print(f"Method error: {e.message}")
except TimeoutError as e:
    # wait_for_request / wait_for_return exceeded timeout
    print(f"Timed out: {e}")
except SDKError as e:
    print(f"SDK error: {e}")
```

### REST API

| HTTP Status | Meaning |
|-------------|---------|
| `400` | Bad request — check request body |
| `404` | Resource not found — check IDs |
| `409` | Conflict — request already in terminal state |
| `500` | Internal server error — check ORB logs |

### MCP

MCP tool errors are returned in the `content` field with `isError: true`. Application-specific error codes:

| Code | Meaning |
|------|---------|
| `1001` | Unknown tool name |
| `1002` | Tool execution failed |
| `1003` | Unknown resource URI |
| `1004` | Resource access failed |

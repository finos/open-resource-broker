<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/awslabs/open-resource-broker/main/docs/assets/orb-logo-horizontal-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/awslabs/open-resource-broker/main/docs/assets/orb-logo-horizontal.svg">
    <img alt="Open Resource Broker" src="https://raw.githubusercontent.com/awslabs/open-resource-broker/main/docs/assets/orb-logo-horizontal.svg" width="520">
  </picture>
</p>

<p align="center">
  <strong>Unified API for orchestrating and provisioning compute capacity</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/orb-py/"><img src="https://img.shields.io/pypi/v/orb-py" alt="PyPI Version"></a>
  <a href="https://pypi.org/project/orb-py/"><img src="https://img.shields.io/pypi/pyversions/orb-py" alt="Python Versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/awslabs/open-resource-broker" alt="License"></a>
  <a href="https://github.com/awslabs/open-resource-broker/releases"><img src="https://img.shields.io/github/v/release/awslabs/open-resource-broker" alt="Latest Release"></a>
  <a href="https://deepwiki.com/awslabs/open-resource-broker"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki"></a>
</p>

<p align="center">
  <a href="https://github.com/awslabs/open-resource-broker/actions/workflows/ci-tests.yml"><img src="https://github.com/awslabs/open-resource-broker/actions/workflows/ci-tests.yml/badge.svg" alt="Unit Tests"></a>
  <a href="https://github.com/awslabs/open-resource-broker/actions/workflows/ci-quality.yml"><img src="https://github.com/awslabs/open-resource-broker/actions/workflows/ci-quality.yml/badge.svg" alt="Quality Checks"></a>
  <a href="https://github.com/awslabs/open-resource-broker/actions/workflows/security-code.yml"><img src="https://github.com/awslabs/open-resource-broker/actions/workflows/security-code.yml/badge.svg" alt="Security Scanning"></a>
  <a href="https://codecov.io/gh/awslabs/open-resource-broker"><img src="https://codecov.io/gh/awslabs/open-resource-broker/graph/badge.svg" alt="Coverage"></a>
  <a href="https://github.com/awslabs/open-resource-broker/actions/workflows/docs.yml"><img src="https://github.com/awslabs/open-resource-broker/actions/workflows/docs.yml/badge.svg" alt="Documentation"></a>
</p>

---

ORB is a unified API for orchestrating and provisioning compute capacity programmatically. Define what you need in a template, request it, track it, return it — through a CLI, REST API, Python SDK, or MCP server.

Built for AWS today (EC2, Auto Scaling Groups, SpotFleet, EC2Fleet), with an extensible provider system for adding new cloud backends.

**Provider support:**
- **AWS** — EC2 RunInstances, EC2Fleet, SpotFleet, Auto Scaling Groups
- **Custom** — extensible via [provider registry](docs/root/developer_guide/architecture.md)

**Scheduler support:**
- **HostFactory** — runs as an [IBM Spectrum Symphony provider plugin](#hostfactory-integration)
- **Standalone** — direct usage without an external scheduler

## Quick Start

```bash
pip install orb-py
orb init
orb templates generate
```

### 1. Pick a template

```bash
orb templates list
```

### 2. Request machines

```bash
orb machines request <template-id> 3
```

### 3. Check status

```bash
orb requests status <request-id>
```

### 4. Return machines when done

```bash
orb machines return <machine-id-1> <machine-id-2> ...
```

## Setup

Get ORB installed and configured for your environment.

<details>
<summary>Installation</summary>

### Standard install

```bash
pip install orb-py
```

### With colored CLI output

```bash
pip install "orb-py[cli]"
```

### With REST API server

```bash
pip install "orb-py[api]"
```

### With monitoring and observability

```bash
pip install "orb-py[monitoring]"
```

### Full install (all extras)

```bash
pip install "orb-py[all]"
```

Requires Python 3.10+.

</details>

<details>
<summary>Configuration</summary>

`orb init` creates a `config.json` in a location based on your install type (virtualenv, user install, system install, or development checkout). Override with:

```bash
export ORB_CONFIG_DIR=/path/to/config
```

| Variable | Description |
|---|---|
| `ORB_ROOT_DIR` | Set base directory for all subdirs (config, work, logs, health, scripts) |
| `ORB_CONFIG_DIR` | Override config directory path (takes precedence over `ORB_ROOT_DIR`) |
| `ORB_WORK_DIR` | Override work directory path (takes precedence over `ORB_ROOT_DIR`) |
| `ORB_LOG_DIR` | Override logs directory path (takes precedence over `ORB_ROOT_DIR`) |
| `ORB_HEALTH_DIR` | Override health directory path (takes precedence over `ORB_ROOT_DIR`) |
| `ORB_LOG_LEVEL` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

See the [Configuration Guide](docs/root/user_guide/configuration.md) for path resolution details, environment variables, and REST API server setup.

</details>

<details>
<summary>AWS Provider Setup</summary>

ORB uses boto3's standard credential chain — any method that works with the AWS CLI works with ORB.

```bash
# Verify your credentials are active
aws sts get-caller-identity
```

**Supported credential methods:** AWS CLI profiles, environment variables (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`), IAM instance profiles, SSO (`aws sso login`), and credential process.

### Supported resource types

| Type | Description |
|---|---|
| `RunInstances` | Direct EC2 instance provisioning |
| `EC2Fleet` | Fleet provisioning with mixed instance types |
| `SpotFleet` | Cost-optimized spot instance fleets |
| `AutoScalingGroup` | Managed scaling groups |

See the [AWS Provider Guide](docs/root/user_guide/configuration.md) for required IAM permissions and SpotFleet service-linked role setup.

</details>

## Interfaces

ORB provides four ways to interact with your infrastructure.

<details>
<summary>CLI Reference</summary>

All available commands and flags.

| Command | Description |
|---|---|
| `orb init` | Initialize config and discover AWS infrastructure |
| `orb init --non-interactive` | Initialize without interactive prompts |
| `orb templates generate` | Generate example templates for your provider |
| `orb templates list` | List available templates |
| `orb templates list --format table` | Table view |
| `orb templates show <template-id>` | Show a single template |
| `orb templates validate --file <file>` | Validate a template file |
| `orb machines request <template-id> <n>` | Request n machines |
| `orb machines list` | List active machines |
| `orb machines return <machine-id> [...]` | Return one or more machines |
| `orb requests status <request-id>` | Check request status |
| `orb requests list` | List all requests |
| `orb infrastructure show` | Show configured infrastructure |
| `orb infrastructure discover` | Scan AWS for VPCs, subnets, security groups |
| `orb infrastructure validate` | Verify infrastructure still exists in AWS |
| `orb config show` | Show current configuration |
| `orb config validate` | Validate configuration |
| `orb providers list` | List configured providers |
| `orb system health` | System health check |
| `orb system health --detailed` | Detailed health check |

Request status values: `pending`, `in_progress`, `completed`, `failed`, `cancelled`, `partial`, `timeout`.

See the [CLI Reference](docs/root/cli/cli-reference.md) for the full flag reference.

</details>

<details>
<summary>REST API</summary>

Example API calls. Requires `pip install "orb-py[api]"` and `orb system serve`.

```bash
# Get available templates
curl -X GET "http://localhost:8000/api/v1/templates"

# Create machine request
curl -X POST "http://localhost:8000/api/v1/requests" \
  -H "Content-Type: application/json" \
  -d '{"templateId": "my-template", "maxNumber": 5}'

# Check request status
curl -X GET "http://localhost:8000/api/v1/requests/req-12345"
```

</details>

<details>
<summary>Python SDK</summary>

Async-first programmatic access via `ORBClient`.

```python
from orb import ORBClient as orb

async with orb(provider="aws") as sdk:
    # List templates
    templates = await sdk.list_templates(active_only=True)

    # Request machines
    request = await sdk.request_machines(
        template_id=templates[0]["template_id"],
        count=3
    )

    # Check status
    status = await sdk.get_request_status(request_id=request["created_request_id"])
```

See the [SDK Quickstart](docs/root/sdk/quickstart.md) for the full guide.

</details>

<details>
<summary>MCP Server (AI Assistant Integration)</summary>

ORB provides a Model Context Protocol (MCP) server for AI assistant integration:

```bash
# Start MCP server in stdio mode (for AI assistants)
orb mcp serve --stdio

# Start as TCP server (for development/testing)
orb mcp serve --port 3000 --host localhost
```

**Available MCP Tools:**
- Provider Management: `check_provider_health`, `list_providers`, `get_provider_config`
- Template Operations: `list_templates`, `get_template`, `validate_template`
- Infrastructure Requests: `request_machines`, `get_request_status`, `return_machines`

**Available MCP Resources:**
- `templates://` — Available compute templates
- `requests://` — Provisioning requests
- `machines://` — Compute instances
- `providers://` — Cloud providers

**Claude Desktop Configuration:**
```json
{
  "mcpServers": {
    "open-resource-broker": {
      "command": "orb",
      "args": ["mcp", "serve", "--stdio"]
    }
  }
}
```

</details>

## Integrations

Connect ORB to schedulers and container platforms.

<details>
<summary>HostFactory Integration</summary>

ORB integrates with IBM Spectrum Symphony as a HostFactory provider plugin, providing full API compatibility through shell scripts:

| Script | Description |
|---|---|
| `getAvailableTemplates.sh` | List available compute templates |
| `requestMachines.sh` | Request new compute instances |
| `getRequestStatus.sh` | Poll request status |
| `requestReturnMachines.sh` | Return instances |
| `getReturnRequests.sh` | Check return request status |

Scripts are available for both Linux (bash) and Windows (bat). They are generated automatically by `orb init` and placed in your config directory.

**Key features:**
- Full HostFactory API compatibility
- Automatic CPU and RAM attribute generation from AWS instance types
- Native HostFactory output format (camelCase JSON)
- Drop-in replacement for existing provider plugins

Example template output:

```json
{
  "templates": [
    {
      "templateId": "t3-medium-template",
      "maxNumber": 5,
      "attributes": {
        "type": ["String", "X86_64"],
        "ncpus": ["Numeric", "2"],
        "nram": ["Numeric", "4096"]
      }
    }
  ]
}
```

See the [HostFactory Guide](docs/root/hostfactory/integration_guide.md) for full integration details.

</details>

<details>
<summary>Docker Deployment</summary>

Run ORB as a containerized service.

```bash
git clone https://github.com/awslabs/open-resource-broker.git
cd open-resource-broker
cp .env.example .env
# Edit .env with your configuration
docker-compose up -d
curl http://localhost:8000/health
```

</details>

## Project

Architecture, development, and documentation.

<details>
<summary>Architecture</summary>

ORB is built on Clean Architecture with Domain-Driven Design (DDD) and CQRS:

- **Domain layer** — pure business logic, no infrastructure dependencies
- **Application layer** — command/query handlers using abstract ports
- **Infrastructure layer** — AWS adapters, DI container, storage strategies
- **Interface layer** — CLI, REST API, MCP server

The **provider system** uses a Strategy/Registry pattern — each cloud provider (AWS, future providers) registers its own strategy, handlers, and template format. The **scheduler system** uses the same pattern — HostFactory and Default schedulers are interchangeable strategies behind a common port.

See the [Architecture Guide](docs/root/developer_guide/architecture.md) for details.

</details>

<details>
<summary>Development</summary>

Set up a local development environment.

```bash
git clone https://github.com/awslabs/open-resource-broker.git
cd open-resource-broker
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the test suite:

```bash
make test
```

Lint and format:

```bash
make lint
make format
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development guide.

</details>

<details>
<summary>Documentation & CI</summary>

- [Quick Start](docs/root/getting_started/quick_start.md)
- [CLI Reference](docs/root/cli/cli-reference.md)
- [Configuration Guide](docs/root/user_guide/configuration.md)
- [Template Management](docs/root/user_guide/templates.md)
- [Troubleshooting](docs/root/user_guide/troubleshooting.md)
- [Architecture](docs/root/developer_guide/architecture.md)
- [API Reference](docs/root/api/readme.md)
- [Deployment](docs/root/deployment/readme.md)
- [DeepWiki](https://deepwiki.com/awslabs/open-resource-broker) — AI-generated codebase documentation

Full docs: [awslabs.github.io/open-resource-broker](https://awslabs.github.io/open-resource-broker/)

</details>

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## Security

See [SECURITY.md](SECURITY.md) for responsible disclosure procedures.

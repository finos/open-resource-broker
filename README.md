<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/orb-logo-horizontal-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/orb-logo-horizontal.svg">
    <img alt="Open Resource Broker" src="docs/assets/orb-logo-horizontal.svg" width="520">
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

---

ORB is a unified API for orchestrating and provisioning compute capacity programmatically. Define what you need in a template, request it, track it, return it — through a CLI, REST API, Python SDK, or MCP server.

Built for AWS today (EC2, Auto Scaling Groups, SpotFleet, EC2Fleet), with an extensible provider system for adding new cloud backends.

| Provider | Resource Types | Status |
|---|---|---|
| **AWS** | EC2 RunInstances, EC2Fleet, SpotFleet, Auto Scaling Groups | Supported |
| *Custom* | Extensible via provider registry | [Guide](docs/root/developer_guide/architecture.md) |

**Scheduler support:**
- **HostFactory** — runs as an [IBM Spectrum Symphony provider plugin](#hostfactory-integration)
- **Standalone** — direct usage without an external scheduler

**Interface modes** (ordered by typical usage):
- **CLI** — primary interface for all operations
- **REST API** — HTTP endpoints for service integration (`pip install "orb-py[api]"`)
- **Python SDK** — async-first programmatic access (`from orb import ORBClient`)
- **MCP Server** — AI assistant integration via Model Context Protocol

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
orb machines return --request-id <request-id>
```

---

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

### Development install

```bash
git clone https://github.com/awslabs/open-resource-broker.git
cd open-resource-broker
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Requires Python 3.10+.

</details>

---

<details>
<summary>CLI Reference</summary>

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
| `orb machines request <template-id> <n> --wait` | Request and wait until ready |
| `orb machines list` | List active machines |
| `orb machines return --request-id <id>` | Return machines from a request |
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

---

<details>
<summary>AWS Provider Setup</summary>

### Prerequisites

- AWS credentials configured via `aws configure`, an IAM instance profile, or environment variables:

  ```bash
  export AWS_ACCESS_KEY_ID=...
  export AWS_SECRET_ACCESS_KEY=...
  export AWS_DEFAULT_REGION=us-east-1
  ```

- Verify credentials are active:

  ```bash
  aws sts get-caller-identity
  ```

### Supported resource types

| Type | Description |
|---|---|
| `RunInstances` | Direct EC2 instance provisioning |
| `EC2Fleet` | Fleet provisioning with mixed instance types |
| `SpotFleet` | Cost-optimized spot instance fleets |
| `AutoScalingGroup` | Managed scaling groups |

### Required IAM permissions

<details>
<summary>IAM policy JSON</summary>

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeImages",
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceTypes",
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:CreateFleet",
        "ec2:DeleteFleet",
        "ec2:DescribeFleets",
        "ec2:RequestSpotFleet",
        "ec2:CancelSpotFleetRequests",
        "ec2:DescribeSpotFleetRequests",
        "ec2:DescribeSpotFleetInstances",
        "ec2:CreateTags",
        "autoscaling:CreateAutoScalingGroup",
        "autoscaling:UpdateAutoScalingGroup",
        "autoscaling:DeleteAutoScalingGroup",
        "autoscaling:DescribeAutoScalingGroups",
        "autoscaling:CreateLaunchConfiguration",
        "autoscaling:DeleteLaunchConfiguration",
        "ec2:CreateLaunchTemplate",
        "ec2:DeleteLaunchTemplate",
        "ec2:DescribeLaunchTemplates",
        "iam:PassRole"
      ],
      "Resource": "*"
    }
  ]
}
```

</details>

For SpotFleet, you also need the `AWSServiceRoleForEC2SpotFleet` service-linked role. If it doesn't exist yet:

```bash
aws iam create-service-linked-role --aws-service-name spotfleet.amazonaws.com
```

</details>

---

<details>
<summary>Configuration</summary>

ORB stores its config in `~/.config/orb/config.json` (Linux/macOS) after `orb init`. Override the location with:

```bash
export ORB_CONFIG_DIR=/path/to/config
```

Key environment variables:

| Variable | Description |
|---|---|
| `ORB_CONFIG_DIR` | Override config directory path |
| `ORB_LOG_LEVEL` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

For the REST API server, copy `.env.example` to `.env` and configure `HF_SERVER_*`, `HF_AUTH_*`, and `HF_STORAGE_*` variables.

See the [Configuration Guide](docs/root/user_guide/configuration.md) for the full reference.

</details>

---

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

---

<details>
<summary>REST API</summary>

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

Start the API server with `pip install "orb-py[api]"` and `orb system serve`.

</details>

---

<details>
<summary>Python SDK</summary>

```python
from orb import ORBClient as orb

async with orb(provider="aws") as sdk:
    # List templates
    templates = await sdk.list_templates(active_only=True)

    # Request machines
    request = await sdk.request_machines(
        template_id=templates[0].template_id,
        count=3
    )

    # Check status
    status = await sdk.get_request_status(request_id=request.id)
```

See the [SDK Quickstart](docs/root/sdk/quickstart.md) for the full guide.

</details>

---

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

---

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

---

<details>
<summary>Docker Deployment</summary>

```bash
git clone https://github.com/awslabs/open-resource-broker.git
cd open-resource-broker
cp .env.example .env
# Edit .env with your configuration
docker-compose up -d
curl http://localhost:8000/health
```

</details>

---

<details>
<summary>Development</summary>

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

---

<details>
<summary>Documentation & CI</summary>

[![Test Matrix](https://github.com/awslabs/open-resource-broker/workflows/Test%20Matrix/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/test-matrix.yml)
[![Quality Checks](https://github.com/awslabs/open-resource-broker/workflows/Quality%20Checks/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/ci-quality.yml)
[![Security Scanning](https://github.com/awslabs/open-resource-broker/workflows/Security%20Scanning/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/security-code.yml)

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

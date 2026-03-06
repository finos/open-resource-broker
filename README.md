# Open Resource Broker

[![Test Matrix](https://github.com/awslabs/open-resource-broker/workflows/Test%20Matrix/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/test-matrix.yml)
[![Quality Checks](https://github.com/awslabs/open-resource-broker/workflows/Quality%20Checks/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/ci-quality.yml)
[![Security Scanning](https://github.com/awslabs/open-resource-broker/workflows/Security%20Scanning/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/security-code.yml)
[![Latest Release](https://img.shields.io/github/v/release/awslabs/open-resource-broker)](https://github.com/awslabs/open-resource-broker/releases)
[![PyPI Version](https://img.shields.io/pypi/v/orb-py)](https://pypi.org/project/orb-py/)
[![License](https://img.shields.io/github/license/awslabs/open-resource-broker)](LICENSE)

Open Resource Broker (ORB) — dynamic cloud resource provisioning via CLI and optional REST API.

ORB lets you request, track, and return cloud compute resources through a single CLI. It supports AWS (EC2, Auto Scaling Groups, SpotFleet, EC2Fleet) and is designed to be extended to additional providers. Resources are provisioned on demand and returned when no longer needed.

## Quick Start

```bash
pip install orb-py
orb init
orb templates generate
orb templates list
orb machines request <template-id> 3
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

The provider and scheduler systems use Strategy/Registry patterns, making it straightforward to add new cloud providers or scheduler integrations.

See the [Architecture Guide](docs/root/developer_guide/architecture.md) for details.

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

## Documentation

- [Quick Start](docs/root/getting_started/quick_start.md)
- [CLI Reference](docs/root/cli/cli-reference.md)
- [Configuration Guide](docs/root/user_guide/configuration.md)
- [Template Management](docs/root/user_guide/templates.md)
- [Troubleshooting](docs/root/user_guide/troubleshooting.md)
- [Architecture](docs/root/developer_guide/architecture.md)
- [API Reference](docs/root/api/readme.md)
- [Deployment](docs/root/deployment/readme.md)

Full docs: [awslabs.github.io/open-resource-broker](https://awslabs.github.io/open-resource-broker/)

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## Security

See [SECURITY.md](SECURITY.md) for responsible disclosure procedures.

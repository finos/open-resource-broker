# Open Resource Broker

[![Test Matrix](https://github.com/awslabs/open-resource-broker/workflows/Test%20Matrix/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/test-matrix.yml)
[![Quality Checks](https://github.com/awslabs/open-resource-broker/workflows/Quality%20Checks/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/ci-quality.yml)
[![Security Scanning](https://github.com/awslabs/open-resource-broker/workflows/Security%20Scanning/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/security-code.yml)
[![Latest Release](https://img.shields.io/github/v/release/awslabs/open-resource-broker)](https://github.com/awslabs/open-resource-broker/releases)
[![PyPI Version](https://img.shields.io/pypi/v/orb-py)](https://pypi.org/project/orb-py/)
[![License](https://img.shields.io/github/license/awslabs/open-resource-broker)](LICENSE)

A cloud provider integration plugin for IBM Spectrum Symphony Host Factory, enabling dynamic provisioning of AWS compute resources via a CLI and REST API.

**Supported providers:** AWS (RunInstances, EC2Fleet, SpotFleet, Auto Scaling Groups)

---

## Prerequisites

Before you start, make sure you have:

- Python 3.10 or higher
- AWS credentials configured (see below)
- An AWS account with the IAM permissions listed below

### AWS credentials

ORB uses the standard AWS credential chain. The simplest setup:

```bash
aws configure
```

Or use an IAM instance profile if running on EC2, or set environment variables:

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
```

Verify your credentials work before proceeding:

```bash
aws sts get-caller-identity
```

### Required IAM permissions

Your AWS identity needs at minimum:

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

For SpotFleet, you also need the `AWSServiceRoleForEC2SpotFleet` service-linked role in your account. If it doesn't exist yet:

```bash
aws iam create-service-linked-role --aws-service-name spotfleet.amazonaws.com
```

---

## Installation

```bash
pip install orb-py
```

For colored CLI output:

```bash
pip install orb-py[cli]
```

Verify the install:

```bash
orb --version
```

---

## Getting started

This walkthrough takes you from a fresh install to your first machine request.

### Step 1: Initialize

Run `orb init` interactively. It discovers your AWS VPCs, subnets, and security groups and writes them into the config so templates work out of the box:

```bash
orb init
```

Follow the prompts to select your region, VPC, subnets, and security groups. When it asks for a SpotFleet IAM role, provide the ARN of a role that has the `AmazonEC2SpotFleetTaggingRole` managed policy attached, or press Enter to skip if you won't use SpotFleet.

If you need non-interactive mode (CI/CD), pass infrastructure details explicitly — otherwise `machines request` will fail with placeholder subnet/SG values:

```bash
orb init --non-interactive \
  --region us-east-1 \
  --subnet-ids subnet-0abc1234,subnet-0def5678 \
  --security-group-ids sg-0abc1234 \
  --fleet-role arn:aws:iam::123456789012:role/MySpotFleetRole
```

### Step 2: Verify infrastructure

Confirm ORB picked up your VPC config:

```bash
orb infrastructure show
```

If subnets or security groups are missing, run discovery:

```bash
orb infrastructure discover
```

### Step 3: Generate templates

Generate example templates for your configured provider:

```bash
orb templates generate
```

### Step 4: List templates and copy a template ID

```bash
orb templates list
```

For a more readable view:

```bash
orb templates list --format table
```

Copy one of the template IDs from the output — you'll use it in the next step.

### Step 5: Request machines

```bash
orb machines request <template-id> 1
```

For example:

```bash
orb machines request aws-ec2fleet-basic 1
```

The command returns a request ID. Check its status:

```bash
orb requests status <request-id>
```

Status values: `pending`, `in_progress`, `completed`, `failed`, `cancelled`, `partial`, `timeout`.

### Step 6: Return machines when done

```bash
orb machines return --request-id <request-id>
```

---

## Common CLI commands

```bash
# Configuration
orb config show                          # Show current config
orb config validate                      # Validate config

# Templates
orb templates list                       # List all templates
orb templates list --format table        # Table view
orb templates show <template-id>         # Show one template
orb templates generate                   # Generate example templates
orb templates validate --file t.json     # Validate a template file

# Machines and requests
orb machines request <template-id> <n>   # Request n machines
orb requests status <request-id>         # Check request status
orb machines list                        # List active machines
orb machines return --request-id <id>    # Return machines

# Infrastructure
orb infrastructure show                  # Show configured infra
orb infrastructure discover              # Scan AWS for VPCs/subnets/SGs
orb infrastructure validate              # Verify infra still exists in AWS

# Providers
orb providers list                       # List configured providers
```

---

## Configuration

ORB stores its config in `~/.config/orb/config.json` (Linux/macOS) after `orb init`. You can override the location:

```bash
export ORB_CONFIG_DIR=/path/to/config
```

Key environment variables:

```bash
ORB_LOG_LEVEL=DEBUG          # Logging level (DEBUG, INFO, WARNING, ERROR)
ORB_AWS_REGION=us-east-1     # AWS region override
ORB_AWS_PROFILE=production   # AWS credential profile override
```

See [Configuration Guide](docs/root/user_guide/configuration.md) for the full reference.

---

## Troubleshooting

**AWS credentials error**

```bash
aws sts get-caller-identity   # Verify credentials are active
```

**Templates not loading after init**

If `orb templates list` returns empty after `orb templates generate`, your provider config may be missing subnet/SG values. Check:

```bash
orb config show
orb infrastructure show
```

If subnets are empty, re-run `orb init` (interactive) or `orb infrastructure discover`.

**`machines request` fails with subnet/SG errors**

This happens when `orb init --non-interactive` was used without passing `--subnet-ids` and `--security-group-ids`. The generated templates contain placeholder values. Fix by running interactive init or passing the flags explicitly.

**Permission denied errors from AWS**

Check that your IAM identity has the permissions listed in the Prerequisites section above. For SpotFleet specifically, verify the service-linked role exists:

```bash
aws iam get-role --role-name AWSServiceRoleForEC2SpotFleet
```

**Debug logging**

```bash
ORB_LOG_LEVEL=DEBUG orb <command>
```

**Validate your setup**

```bash
orb config validate
orb infrastructure validate
```

See [Troubleshooting Guide](docs/root/user_guide/troubleshooting.md) for more.

---

## Architecture

ORB implements Clean Architecture with Domain-Driven Design (DDD) and CQRS:

- **Domain layer** — pure business logic, no infrastructure dependencies
- **Application layer** — command/query handlers using abstract ports
- **Infrastructure layer** — AWS adapters, DI container, storage strategies
- **Interface layer** — CLI, REST API, MCP server

See [Architecture Guide](docs/root/developer_guide/architecture.md) for details.

---

## Development

```bash
git clone https://github.com/awslabs/open-resource-broker.git
cd open-resource-broker

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"

make test       # Run tests
make lint       # Check code quality
make format     # Format code
```

---

## Documentation

- [Quick Start](docs/root/getting_started/quick_start.md)
- [CLI Reference](docs/root/cli/cli-reference.md)
- [Configuration Guide](docs/root/user_guide/configuration.md)
- [Template Management](docs/root/user_guide/templates.md)
- [Troubleshooting](docs/root/user_guide/troubleshooting.md)
- [Architecture](docs/root/developer_guide/architecture.md)
- [Deployment](docs/root/deployment/readme.md)

---

## HostFactory integration

ORB is designed for seamless integration with IBM Spectrum Symphony Host Factory. It is fully compatible with the HostFactory API, generates correct CPU/RAM attributes from AWS instance types, and supports both camelCase (legacy) and snake_case output formats.

See [HostFactory Integration Guide](docs/root/hostfactory/integration_guide.md) for details.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## Security

See [SECURITY.md](SECURITY.md) for responsible disclosure procedures.

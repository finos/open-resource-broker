# Installation Guide

This guide covers installing the Open Resource Broker (ORB) and getting it running with the `orb` CLI.

## Prerequisites

- Python 3.10 or higher
- AWS credentials configured (profile or IAM role)
- Access to EC2 and related AWS services

## Install

ORB is published to PyPI as `orb-py`. Install the base package or include optional feature groups:

```bash
# Minimal install (CLI only)
pip install orb-py

# With colored terminal output
pip install "orb-py[cli]"

# With REST API server
pip install "orb-py[api]"

# With monitoring (OpenTelemetry, Prometheus)
pip install "orb-py[monitoring]"

# Everything
pip install "orb-py[all]"
```

Optional dependency groups:

| Group | Adds | Use when |
|-------|------|----------|
| `cli` | `rich`, `rich-argparse` | You want colored, formatted terminal output |
| `api` | `fastapi`, `uvicorn`, `jinja2` | You want to run the REST API server |
| `monitoring` | `opentelemetry-*`, `prometheus-client`, `psutil` | Production deployments with observability |
| `all` | All of the above | Full-featured install |

Verify the install:

```bash
orb --version
```

## Development Install

```bash
git clone https://github.com/awslabs/open-resource-broker.git
cd open-resource-broker

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -e ".[dev]"

orb --version
```

## Initialize ORB

After installing, run `orb init` to create the configuration:

```bash
# Interactive — ORB walks you through provider, region, and infrastructure selection
orb init

# Non-interactive — supply all values as flags (useful for CI/scripted setup)
orb init --non-interactive \
  --provider aws \
  --region us-east-1 \
  --profile default \
  --scheduler hostfactory \
  --subnet-ids subnet-aaa111,subnet-bbb222 \
  --security-group-ids sg-11111111 \
  --fleet-role arn:aws:iam::123456789012:role/SpotFleetRole
```

`orb init` flags:

| Flag | Description | Default |
|------|-------------|---------|
| `--non-interactive` | Skip all prompts | false |
| `--provider` | Provider type | `aws` |
| `--region` | AWS region | prompted |
| `--profile` | AWS profile | prompted |
| `--scheduler` | Scheduler type (`default`, `hostfactory`) | prompted |
| `--config-dir` | Custom config directory | OS default |
| `--subnet-ids` | Comma-separated subnet IDs (non-interactive only) | — |
| `--security-group-ids` | Comma-separated security group IDs (non-interactive only) | — |
| `--fleet-role` | Spot Fleet IAM role ARN (non-interactive only) | — |
| `--force` | Overwrite existing config | false |

`--subnet-ids`, `--security-group-ids`, and `--fleet-role` are only used with `--non-interactive`. In interactive mode ORB discovers these from your AWS account automatically.

## Basic Usage

```bash
# Generate example templates for your provider
orb templates generate

# List available templates
orb templates list

# Request machines
orb machines request <template-id> <count>

# Check request status
orb requests status <request-id>

# Show system health
orb system health
```

## Verify Infrastructure

After init, confirm ORB can see your AWS infrastructure:

```bash
# Show what infrastructure ORB is configured to use
orb infrastructure show

# Discover available VPCs, subnets, and security groups
orb infrastructure discover

# Validate configured resources still exist in AWS
orb infrastructure validate
```

## Troubleshooting

**Python version**
```bash
python --version  # must be 3.10+
```

**AWS credentials not found**

Ensure your AWS profile is configured:
```bash
aws configure list
aws sts get-caller-identity
```

Or set environment variables:
```bash
export AWS_PROFILE=my-profile
export AWS_DEFAULT_REGION=us-east-1
```

**Config not loading**

Show the active configuration:
```bash
orb config show
```

Validate it:
```bash
orb config validate
```

**Re-run infrastructure discovery**

If subnets or security groups change after init:
```bash
orb infrastructure discover
```

## Next Steps

- [CLI Reference](../cli/cli-reference.md) — all commands and flags
- [Infrastructure Commands](../cli/infrastructure-commands.md) — discover and validate AWS infrastructure
- [Configuration](configuration.md) — configure ORB for your environment
- [Templates](templates.md) — set up compute templates

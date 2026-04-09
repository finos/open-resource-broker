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

## Directory Layout

These variables control where ORB stores its files. All are optional — ORB derives sensible defaults from the install location automatically.

| Variable | Default | Description |
|---|---|---|
| `ORB_ROOT_DIR` | Derived from install type (see below) | Root directory for all ORB data. Setting this overrides all other directory defaults. |
| `ORB_CONFIG_DIR` | `$ORB_ROOT_DIR/config` | Configuration files (`config.json`, templates). |
| `ORB_WORK_DIR` | `$ORB_ROOT_DIR/work` | Working data (request state, provider output). |
| `ORB_LOG_DIR` | `$ORB_ROOT_DIR/logs` | ORB process log files. |
| `ORB_SCRIPTS_DIR` | `$ORB_ROOT_DIR/scripts` | Provider scripts (e.g. `invoke_provider.sh` for HostFactory). |
| `ORB_VENV_PATH` | _(unset)_ | Path to a Python virtualenv. When set, `invoke_provider.sh` activates it before running `orb`. |
| `ORB_HEALTH_DIR` | `$ORB_WORK_DIR/health` | Health-check output files written by `orb system health`. |
| `ORB_CACHE_DIR` | `$ORB_WORK_DIR/.cache` | Internal cache (template resolution, provider metadata). |

### Default root location by install type

| Install type | Default `ORB_ROOT_DIR` |
|---|---|
| virtualenv (standard) | Parent of the venv directory |
| virtualenv (uv tool / mise) | `~/.orb` |
| `pip install --user` | `~/.orb` |
| System install (`/usr`, `/opt`) | `$sys.prefix/orb` (falls back to `~/.orb` if not writable) |
| Development (pyproject.toml found) | Repository root |

### Install scenarios

#### System install (root)

```bash
sudo pip install orb-py
sudo orb init --non-interactive \
  --provider aws --region us-east-1 \
  --subnet-ids subnet-aaa111 \
  --security-group-ids sg-11111111
```

ORB writes config to `/usr/orb/config/` (or `/opt/orb/config/` depending on your Python prefix). Override with `ORB_ROOT_DIR` if needed:

```bash
sudo ORB_ROOT_DIR=/etc/orb orb init --non-interactive ...
```

#### System install (non-root)

When the system prefix is not writable, ORB automatically falls back to `~/.orb`:

```bash
pip install orb-py          # system Python, no sudo
orb init                    # writes to ~/.orb/config/
```

Or pin the location explicitly:

```bash
export ORB_ROOT_DIR=~/.orb
orb init --non-interactive --provider aws --region us-east-1 \
  --subnet-ids subnet-aaa111 --security-group-ids sg-11111111
```

#### Virtualenv install

```bash
python -m venv .venv
source .venv/bin/activate
pip install "orb-py[all]"
orb init
```

ORB detects the venv and uses its parent directory as the root, so config lands next to your project:

```
my-project/
  .venv/
  orb/config/config.json   ← written here
  orb/logs/
  orb/work/
```

#### Install with --prefix

```bash
pip install --prefix /opt/myapp orb-py
export ORB_ROOT_DIR=/opt/myapp/orb
export PATH="/opt/myapp/bin:$PATH"
orb init --non-interactive --provider aws --region us-east-1 \
  --subnet-ids subnet-aaa111 --security-group-ids sg-11111111
```

## Next Steps

- [CLI Reference](../cli/cli-reference.md) — all commands and flags
- [Infrastructure Commands](../cli/infrastructure-commands.md) — discover and validate AWS infrastructure
- [Configuration](configuration.md) — configure ORB for your environment
- [Templates](templates.md) — set up compute templates

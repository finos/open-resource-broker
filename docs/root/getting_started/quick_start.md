# Quick Start Guide

Get up and running with the Open Resource Broker in minutes.

## Prerequisites

- Python 3.10 or higher
- AWS credentials configured (`~/.aws/credentials` or an IAM instance profile)
- IAM permissions for EC2, EC2Fleet, SpotFleet, and Auto Scaling — see the [README](https://github.com/awslabs/open-resource-broker#required-iam-permissions) for the full policy

## Installation

### Standard install

```bash
pip install orb-py
```

### Development install

```bash
pip install -e ".[dev]"
```

## Step 1: Initialize

Run `orb init` to create the configuration directory and set up your first provider.

By default, `orb init` runs interactively and discovers your AWS VPCs, subnets, and security groups:

```bash
orb init
```

For CI/CD pipelines or scripted environments, skip AWS discovery with `--non-interactive`:

```bash
orb init --non-interactive
```

You can also pass infrastructure details directly in non-interactive mode:

```bash
orb init --non-interactive \
  --region us-east-1 \
  --profile my-aws-profile \
  --subnet-ids subnet-0abc1234,subnet-0def5678 \
  --security-group-ids sg-0abc1234 \
  --fleet-role arn:aws:iam::123456789012:role/MySpotFleetRole
```

## Step 2: Generate templates

Generate example templates for your configured provider:

```bash
orb templates generate
```

This writes template files to your ORB work directory. Templates are scoped to a provider type (e.g., AWS EC2Fleet, SpotFleet) and reusable across provider instances.

## Step 3: List templates

Verify the templates loaded correctly:

```bash
orb templates list
```

For a more readable view:

```bash
orb templates list --format table
```

## Step 4: Request machines

Copy a template ID from the output of `orb templates list`, then request machines:

```bash
orb machines request <template-id> 3
```

Or using flags:

```bash
orb machines request --template-id <template-id> --count 3
```

The command returns a request ID. Use `--wait` to block until machines are ready:

```bash
orb machines request aws-ec2fleet-basic 3 --wait --timeout 600
```

## Step 5: Check request status

```bash
orb requests status req-abc123
```

Request status values: `pending`, `in_progress`, `completed`, `failed`, `cancelled`, `partial`, `timeout`.

## Step 6: View infrastructure

Show what infrastructure ORB is configured to use:

```bash
orb infrastructure show
```

To scan your AWS account for available VPCs, subnets, and security groups:

```bash
orb infrastructure discover
```

## Step 7: View configuration

```bash
orb config show
```

## Returning machines

When you're done with machines, return them:

```bash
orb machines return i-0abc1234def567890 i-0def5678abc123456
```

## Troubleshooting

**AWS permissions error** — verify your credentials are active:
```bash
aws sts get-caller-identity
```

**No templates loaded** — re-run generate and check your provider config:
```bash
orb templates generate
orb config show
```

**System health check:**
```bash
orb system health --detailed
```

## Next steps

- [CLI Reference](../cli/cli-reference.md) — full command and flag reference
- [Configuration Guide](../user_guide/configuration.md) — advanced provider and storage config
- [Template Management](../user_guide/templates.md) — creating and customizing templates

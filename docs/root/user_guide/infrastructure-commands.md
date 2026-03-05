# Infrastructure Commands

Reference for the `orb infrastructure` command group. Use these commands to discover, inspect, and validate the AWS infrastructure ORB uses when provisioning machines.

`orb infra` is an alias for `orb infrastructure` — all subcommands work with either prefix.

## Overview

ORB needs to know which subnets and security groups to use when launching instances. These values live in `template_defaults` inside `config.json` and are applied at runtime to every provisioning request.

| Command | Purpose |
|---------|---------|
| `orb infrastructure discover` | Scan AWS and update `template_defaults` with your selections |
| `orb infrastructure show` | Display what infrastructure ORB is currently configured to use |
| `orb infrastructure validate` | Verify configured resources still exist in AWS |

---

## `orb infrastructure discover`

Scan your AWS account for available VPCs, subnets, and security groups.

**Usage:**
```bash
orb infrastructure discover [OPTIONS]
orb infra discover [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--show` | Display specific resource types without updating config: `vpcs`, `subnets`, `security-groups` (or `sg`), or `all` | `--show subnets` |
| `--summary` | Show only counts per resource type, no details | `--summary` |
| `--all-providers` | Run discovery across all configured providers | `--all-providers` |
| `--provider` | Run discovery for a specific provider instance | `--provider aws-prod` |
| `--region` | AWS region override | `--region eu-west-1` |
| `--profile` | AWS profile override | `--profile production` |

**Examples:**
```bash
# Interactive discovery — select subnets and security groups to configure
orb infrastructure discover

# Preview available resources without changing config
orb infrastructure discover --show all

# Show only subnets
orb infrastructure discover --show subnets

# Show only security groups
orb infrastructure discover --show security-groups

# Summary counts only
orb infrastructure discover --summary

# Discover for a specific provider
orb infrastructure discover --provider aws-prod

# Discover across all providers
orb infrastructure discover --all-providers
```

When you confirm your selections, ORB writes the chosen IDs into `template_defaults` in `config.json`:

```json
{
  "provider": {
    "providers": [
      {
        "name": "aws-prod",
        "provider_type": "aws",
        "template_defaults": {
          "subnet_ids": ["subnet-aaa111", "subnet-bbb222"],
          "security_group_ids": ["sg-11111111"],
          "fleet_role": "arn:aws:iam::123456789012:role/SpotFleetRole"
        }
      }
    ]
  }
}
```

These values are applied at runtime — they are not baked into individual template files. Updating `template_defaults` once affects all templates automatically.

---

## `orb infrastructure show`

Display the infrastructure ORB is currently configured to use.

**Usage:**
```bash
orb infrastructure show [OPTIONS]
orb infra show [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--all-providers` | Show configuration for all providers | `--all-providers` |
| `--provider` | Show configuration for a specific provider | `--provider aws-prod` |
| `--format` | Output format (`json`, `yaml`, `table`) | `--format table` |

**Examples:**
```bash
# Show current infrastructure config
orb infrastructure show

# Show in table format
orb infrastructure show --format table

# Show for all providers
orb infrastructure show --all-providers
```

**Example output:**
```json
{
  "provider": "aws-prod",
  "subnet_ids": ["subnet-aaa111", "subnet-bbb222"],
  "security_group_ids": ["sg-11111111"],
  "fleet_role": "arn:aws:iam::123456789012:role/SpotFleetRole"
}
```

If no infrastructure has been configured (for example after `orb init --non-interactive` without the infrastructure flags), the output will indicate that no defaults are set.

---

## `orb infrastructure validate`

Verify that the subnets, security groups, and fleet role in `template_defaults` still exist in your AWS account.

**Usage:**
```bash
orb infrastructure validate [OPTIONS]
orb infra validate [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--provider` | Validate for a specific provider | `--provider aws-prod` |
| `--region` | AWS region override | `--region us-east-1` |
| `--profile` | AWS profile override | `--profile production` |
| `--format` | Output format (`json`, `yaml`, `table`) | `--format table` |

**Examples:**
```bash
# Validate configured infrastructure
orb infrastructure validate

# Validate for a specific provider
orb infrastructure validate --provider aws-prod
```

Validation checks that each configured resource ID resolves in AWS. Resources that no longer exist are reported so you can re-run discovery and update your config.

---

## When to use each command

| Scenario | Recommended command |
|----------|---------------------|
| First-time setup | `orb init` (interactive) — runs discovery as part of the wizard |
| Infrastructure changed (new subnets, new SGs) | `orb infrastructure discover` |
| Scripted/CI setup with known resource IDs | `orb init --non-interactive --subnet-ids ... --security-group-ids ...` |
| Check what is currently configured | `orb infrastructure show` |
| Confirm resources still exist after infra changes | `orb infrastructure validate` |

---

## Related

- [CLI Reference](../cli/cli-reference.md) — full command reference including `orb init` flags
- [Installation](installation.md) — initial setup with `orb init`
- `orb infrastructure --help` — inline help for all subcommands

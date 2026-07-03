# Multi-Provider Configuration

This guide covers configuring multiple cloud provider instances for the Open Resource Broker.

## Provider Naming Convention

Provider instances follow the pattern: `{type}_{profile}_{region}`

**Examples:**
- `aws_default_us-east-1` - AWS default profile in us-east-1
- `aws_prod_us-west-2` - AWS production profile in us-west-2  
- `aws_dev_eu-west-1` - AWS development profile in eu-west-1

## Configuration

### Provider Configuration File

```yaml
# config/providers.yml
providers:
  - name: aws-primary
    type: aws
    config:
      region: us-east-1
      profile: default
      handlers:
        default: ec2_fleet
        spot_fleet:
          enabled: true
        auto_scaling_group:
          enabled: true
    template_defaults:
      # Default template settings
```

### Multi-Provider Operations

```bash
# Generate templates for all active providers
orb templates generate --all-providers

# Generate templates for specific provider instance
orb templates generate --provider-name aws_prod_us-west-2

# Generate templates for specific provider API
orb templates generate --provider-api EC2Fleet

# Override provider for any command
orb --provider-name aws_dev_eu-west-1 system health
orb --provider-name aws_prod_us-west-2 requests status req-123
```

## Provider Override

Use the global `--provider-name` flag with any command:

```bash
orb --provider-name aws_prod_us-west-2 templates list
orb --provider-name aws_dev_eu-west-1 machines request template-id 3

# List available provider instances
orb providers list
```

## Template Generation

Templates are generated with provider-specific naming:

- `aws_prod_us-west-2_templates.json`
- `aws_dev_eu-west-1_templates.json`

This ensures templates are organized by provider instance for easy management.

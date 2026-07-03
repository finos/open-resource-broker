# Multi-Provider Template Generation

The `orb templates generate` command supports generating example templates for multiple cloud providers simultaneously, with provider-specific naming conventions and configuration patterns.

## Overview

Multi-provider template generation enables:
- **Automatic template creation** for all active providers
- **Provider-specific naming** following each provider's conventions
- **Selective generation** for specific providers or APIs
- **Consistent template structure** across different cloud providers

## Default Behavior

### Generate for All Active Providers

```bash
# Generates templates for ALL active providers (default behavior)
orb templates generate
```

**Output:**
```
Generated templates for 3 providers:
- aws-prod: 8 templates in aws-prod_templates.json
- aws-dev: 8 templates in aws-dev_templates.json  
- aws-staging: 8 templates in aws-staging_templates.json
```

## Provider-Specific Generation

### Single Provider Instance

```bash
# Generate for specific provider instance
orb templates generate --provider-name aws-prod

# Generate for development environment
orb templates generate --provider-name aws-dev
```

### Specific Provider API

```bash
# Generate templates for EC2Fleet API only
orb templates generate --provider-api EC2Fleet

# Generate templates for SpotFleet API only
orb templates generate --provider-api SpotFleet

# Generate templates for Auto Scaling Groups
orb templates generate --provider-api ASG
```

### Explicit All Providers

```bash
# Explicitly generate for all providers (same as default)
orb templates generate --all-providers
```

## Provider Naming Conventions

### AWS Provider Pattern

**Format:** `{type}_{profile}_{region}`

**Examples:**
- `aws_prod_us-west-2` - Production profile in US West 2
- `aws_dev_eu-west-1` - Development profile in EU West 1
- `aws_staging_us-east-1` - Staging profile in US East 1

**Configuration:**
```json
{
  "name": "aws_prod_us-west-2",
  "type": "aws",
  "config": {
    "profile": "prod",
    "region": "us-west-2"
  }
}
```

### Template File Naming

Generated template files follow provider naming:

```
config/
├── aws_prod_us-west-2_templates.json
├── aws_dev_eu-west-1_templates.json
└── aws_staging_us-east-1_templates.json
```

## Template Structure

### Provider-Specific Templates

Each provider generates templates optimized for its configuration:

**AWS Production Templates:**
```json
{
  "templates": [
    {
      "template_id": "EC2Fleet-Instant-OnDemand-prod",
      "name": "EC2Fleet Instant OnDemand (Production)",
      "provider_name": "aws_prod_us-west-2",
      "provider_api": "EC2Fleet",
      "image_id": "ami-0c02fb55956c7d316",
      "instance_type": "m5.large",
      "region": "us-west-2"
    }
  ]
}
```

**AWS Development Templates:**
```json
{
  "templates": [
    {
      "template_id": "EC2Fleet-Instant-OnDemand-dev", 
      "name": "EC2Fleet Instant OnDemand (Development)",
      "provider_name": "aws_dev_eu-west-1",
      "provider_api": "EC2Fleet",
      "image_id": "ami-0d71ea30463e0ff8d",
      "instance_type": "t3.medium",
      "region": "eu-west-1"
    }
  ]
}
```

## Available Provider APIs

### AWS Provider APIs

| API | Description | Use Case |
|-----|-------------|----------|
| `EC2Fleet` | EC2 Fleet API | Mixed instance types, spot/on-demand |
| `SpotFleet` | Spot Fleet API | Cost-optimized spot instances |
| `ASG` | Auto Scaling Groups | Auto-scaling workloads |
| `RunInstances` | Basic EC2 API | Simple instance launches |

### Template Examples per API

#### EC2Fleet Templates
- `EC2Fleet-Instant-OnDemand` - Instant on-demand instances
- `EC2Fleet-Instant-Spot` - Instant spot instances
- `EC2Fleet-Maintain-OnDemand` - Maintained on-demand fleet
- `EC2Fleet-Maintain-Spot` - Maintained spot fleet

#### SpotFleet Templates
- `SpotFleet-Instant` - Instant spot fleet
- `SpotFleet-Maintain` - Maintained spot fleet

#### Auto Scaling Group Templates
- `ASG-OnDemand` - On-demand auto scaling
- `ASG-Spot` - Spot instance auto scaling

#### RunInstances Templates
- `RunInstances-OnDemand` - Basic on-demand instances

## Command Examples

### Basic Generation

```bash
# Generate for all active providers
$ orb templates generate
{
  "status": "success",
  "message": "Generated templates for 3 providers",
  "providers": [
    {
      "provider": "aws_prod_us-west-2",
      "filename": "aws_prod_us-west-2_templates.json",
      "templates_count": 8,
      "path": "/config/aws_prod_us-west-2_templates.json"
    },
    {
      "provider": "aws_dev_eu-west-1", 
      "filename": "aws_dev_eu-west-1_templates.json",
      "templates_count": 8,
      "path": "/config/aws_dev_eu-west-1_templates.json"
    }
  ]
}
```

### Provider-Specific Generation

```bash
# Generate for production provider only
$ orb templates generate --provider-name aws_prod_us-west-2
{
  "status": "success",
  "message": "Generated templates for 1 provider",
  "providers": [
    {
      "provider": "aws_prod_us-west-2",
      "filename": "aws_prod_us-west-2_templates.json", 
      "templates_count": 8,
      "path": "/config/aws_prod_us-west-2_templates.json"
    }
  ]
}
```

### API-Specific Generation

```bash
# Generate EC2Fleet templates only
$ orb templates generate --provider-api EC2Fleet
{
  "status": "success",
  "message": "Generated EC2Fleet templates for 3 providers",
  "providers": [
    {
      "provider": "aws_prod_us-west-2",
      "filename": "aws_prod_us-west-2_templates.json",
      "templates_count": 4,
      "api_filter": "EC2Fleet"
    }
  ]
}
```

## Configuration Requirements

### Provider Configuration

Each provider must be properly configured:

```json
{
  "provider": {
    "providers": [
      {
        "name": "aws_prod_us-west-2",
        "type": "aws",
        "enabled": true,
        "config": {
          "profile": "prod",
          "region": "us-west-2",
          "role_arn": "arn:aws:iam::123456789012:role/ProdRole"
        }
      },
      {
        "name": "aws_dev_eu-west-1",
        "type": "aws",
        "enabled": true,
        "config": {
          "profile": "dev",
          "region": "eu-west-1"
        }
      }
    ]
  }
}
```

### Scheduler Integration

Templates are generated using the configured scheduler:

**HostFactory Scheduler:**
- Uses HostFactory-compatible field names
- Includes field mapping metadata
- Optimized for IBM Symphony integration

**Default Scheduler:**
- Uses native domain field names
- Direct serialization format
- Optimized for CLI usage

## Template Loading

### Scheduler-Aware Loading

The system loads templates based on the active scheduler:

**HostFactory Scheduler:**
```bash
# Loads: hostfactory_templates.json (if exists)
# Falls back to: {provider}_templates.json
orb --scheduler hostfactory templates list
```

**Default Scheduler:**
```bash
# Loads: default_templates.json (if exists)  
# Falls back to: {provider}_templates.json
orb --scheduler default templates list
```

### Provider-Specific Loading

When using provider override:

```bash
# Loads templates for specific provider
orb --provider-name aws_prod_us-west-2 templates list

# Generates and loads for specific provider
orb --provider-name aws_dev_eu-west-1 templates generate
```

## Advanced Usage

### Combined with Provider Override

```bash
# Generate templates for specific provider, then use them
orb --provider-name aws-prod templates generate --provider-api EC2Fleet
orb --provider-name aws-prod templates list --format table
```

### Combined with Scheduler Override

```bash
# Generate HostFactory-compatible templates
orb --scheduler hostfactory templates generate

# Generate default scheduler templates
orb --scheduler default templates generate
```

### Batch Generation

```bash
# Generate different APIs for different providers
orb templates generate --provider-name aws-prod --provider-api EC2Fleet
orb templates generate --provider-name aws-dev --provider-api SpotFleet
orb templates generate --provider-name aws-staging --provider-api ASG
```

## File Management

### Template File Locations

Templates are generated in the configuration directory:

```
$ORB_CONFIG_DIR/
├── config.json                           # Main configuration
├── aws_prod_us-west-2_templates.json     # Production templates
├── aws_dev_eu-west-1_templates.json      # Development templates
├── aws_staging_us-east-1_templates.json  # Staging templates
├── hostfactory_templates.json            # HostFactory-specific (optional)
└── default_templates.json                # Default scheduler (optional)
```

### Template Discovery

The system discovers templates in this order:

1. **Scheduler-specific file** (`{scheduler}_templates.json`)
2. **Provider-specific file** (`{provider}_templates.json`)
3. **Legacy file** (`templates.json`)

### Template Merging

When multiple template files exist, they are merged with precedence:

1. **Scheduler-specific** (highest priority)
2. **Provider-specific** (medium priority)
3. **Legacy** (lowest priority)

## Error Handling

### Provider Not Found

```bash
$ orb templates generate --provider-name nonexistent-provider
{
  "status": "error",
  "message": "Provider 'nonexistent-provider' not found",
  "available_providers": ["aws-prod", "aws-dev", "aws-staging"]
}
```

### Provider Disabled

```bash
$ orb templates generate --provider-name disabled-provider
{
  "status": "error",
  "message": "Provider 'disabled-provider' is disabled"
}
```

### Invalid API

```bash
$ orb templates generate --provider-api InvalidAPI
{
  "status": "error", 
  "message": "Provider API 'InvalidAPI' not supported",
  "supported_apis": ["EC2Fleet", "SpotFleet", "ASG", "RunInstances"]
}
```

## Best Practices

### Development Workflow

```bash
# 1. Generate templates for development
orb templates generate --provider-name aws-dev

# 2. Test templates
orb --provider-name aws-dev templates list --format table

# 3. Generate for staging
orb templates generate --provider-name aws-staging

# 4. Generate for production
orb templates generate --provider-name aws-prod
```

### Environment-Specific Templates

```bash
# Generate different template sets per environment
orb templates generate --provider-name aws-dev --provider-api SpotFleet    # Cost-optimized dev
orb templates generate --provider-name aws-staging --provider-api EC2Fleet # Mixed staging
orb templates generate --provider-name aws-prod --provider-api ASG         # Auto-scaling prod
```

### Template Maintenance

```bash
# Regenerate all templates after configuration changes
orb templates generate --all-providers

# Update specific provider templates
orb templates generate --provider-name aws-prod

# Refresh template cache after generation
orb templates refresh --force
```

## See Also

- [CLI Reference](cli-reference.md) - Complete command reference
- [Provider Override](provider-override.md) - Provider override functionality
- [Template Commands](template-commands.md) - Template management commands
- [Multi-Provider Configuration](../configuration/multi-provider.md) - Provider configuration guide
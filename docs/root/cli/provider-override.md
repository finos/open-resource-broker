# Provider Override Functionality

The `--provider-name` flag allows you to override the selected provider instance for any command, similar to how `--scheduler` overrides the scheduler strategy.

## Overview

Provider override enables you to:
- Test different provider configurations without changing config files
- Use specific provider instances for individual commands
- Switch between development, staging, and production providers
- Combine with scheduler overrides for complete environment control

## Usage

### Basic Syntax

```bash
orb --provider-name PROVIDER_INSTANCE_NAME [command] [args...]
```

### Provider Instance Names

Provider instances are defined in your configuration file with naming patterns:

**AWS Provider Pattern:** `{type}_{profile}_{region}`
- `aws_prod_us-west-2` - AWS production in US West 2
- `aws_dev_eu-west-1` - AWS development in EU West 1
- `aws_staging_us-east-1` - AWS staging in US East 1

**Custom Naming:**
You can use any naming convention in your configuration:
- `aws-production`
- `aws-development`  
- `primary-aws`
- `backup-aws`

## Examples

### Template Operations

```bash
# List templates using production provider
orb --provider-name aws-prod templates list

# Generate templates for development provider
orb --provider-name aws-dev templates generate

# Show template with specific provider context
orb --provider-name aws-staging templates show aws-basic
```

### Machine Requests

```bash
# Request machines from production provider
orb --provider-name aws-prod machines request aws-basic 5

# Request from development provider with different scheduler
orb --scheduler hostfactory --provider-name aws-dev machines request template-id 3

# Check machine status with specific provider
orb --provider-name aws-prod machines status i-1234567890abcdef0
```

### Request Management

```bash
# Check request status with production provider
orb --provider-name aws-prod requests status req-123

# List requests from development environment
orb --provider-name aws-dev requests list --status pending

# Cancel request with specific provider context
orb --provider-name aws-staging requests cancel req-456
```

### Provider Health Checks

```bash
# Check specific provider health
orb --provider-name aws-prod providers health

# Compare health across providers
orb --provider-name aws-prod providers health --format table
orb --provider-name aws-dev providers health --format table
```

## Configuration Requirements

### Provider Instance Definition

Your configuration file must define the provider instances:

```json
{
  "provider": {
    "providers": [
      {
        "name": "aws-prod",
        "type": "aws",
        "enabled": true,
        "config": {
          "profile": "production",
          "region": "us-west-2",
          "role_arn": "arn:aws:iam::123456789012:role/ProdRole"
        }
      },
      {
        "name": "aws-dev",
        "type": "aws", 
        "enabled": true,
        "config": {
          "profile": "development",
          "region": "us-east-1"
        }
      },
      {
        "name": "aws-staging",
        "type": "aws",
        "enabled": true,
        "config": {
          "profile": "staging",
          "region": "eu-west-1"
        }
      }
    ]
  }
}
```

### Validation

The system validates provider overrides:

1. **Provider exists** - Must be defined in configuration
2. **Provider enabled** - Must not be disabled
3. **Provider compatible** - Should work with requested operations

## Error Handling

### Provider Not Found

```bash
$ orb --provider-name nonexistent-provider templates list
Error: Provider instance 'nonexistent-provider' not found
Available providers: aws-prod, aws-dev, aws-staging
```

### Provider Disabled

```bash
$ orb --provider-name disabled-provider machines request template-id 1
Error: Provider instance 'disabled-provider' is disabled
```

### Configuration Issues

```bash
$ orb --provider-name aws-misconfigured providers health
Error: Provider 'aws-misconfigured' configuration invalid: missing required field 'region'
```

## Precedence Order

When multiple provider selection methods are used:

1. **CLI Override** (`--provider-name`) - Highest precedence
2. **Template Setting** (`provider_name` in template) - Medium precedence  
3. **Configuration Default** - Lowest precedence

### Example

```json
// Template with provider setting
{
  "template_id": "aws-basic",
  "provider_name": "aws-dev",
  // ... other fields
}
```

```bash
# CLI override wins over template setting
orb --provider-name aws-prod machines request aws-basic 1
# Uses aws-prod, not aws-dev from template
```

## Multi-Provider Template Generation

Provider override works with template generation:

### Generate for Specific Provider

```bash
# Generate templates for production provider only
orb --provider-name aws-prod templates generate

# Generate with specific API for development
orb --provider-name aws-dev templates generate --provider-api EC2Fleet
```

### Provider-Aware Template Files

Generated templates are named by provider:

```
config/
├── aws-prod_templates.json     # Production templates
├── aws-dev_templates.json      # Development templates  
└── aws-staging_templates.json  # Staging templates
```

## Combined with Scheduler Override

Provider and scheduler overrides work together:

```bash
# Use HostFactory scheduler with production provider
orb --scheduler hostfactory --provider-name aws-prod machines request template-id 5

# Use default scheduler with development provider
orb --scheduler default --provider-name aws-dev templates list --format table

# Test configuration with specific scheduler and provider
orb --scheduler hf --provider-name aws-staging system health --detailed
```

## Best Practices

### Development Workflow

```bash
# Development commands
orb --provider-name aws-dev templates generate
orb --provider-name aws-dev machines request test-template 1

# Staging validation
orb --provider-name aws-staging machines request prod-template 1 --dry-run

# Production deployment
orb --provider-name aws-prod machines request prod-template 10
```

### Environment Testing

```bash
# Test same template across environments
orb --provider-name aws-dev machines request aws-basic 1
orb --provider-name aws-staging machines request aws-basic 1  
orb --provider-name aws-prod machines request aws-basic 1

# Compare provider health
orb --provider-name aws-dev providers health --format table
orb --provider-name aws-prod providers health --format table
```

### Configuration Validation

```bash
# Validate each provider configuration
orb --provider-name aws-dev config validate
orb --provider-name aws-staging config validate
orb --provider-name aws-prod config validate

# Test connectivity
orb --provider-name aws-dev providers health
orb --provider-name aws-prod providers health
```

## Troubleshooting

### List Available Providers

```bash
# See all configured providers
orb providers list --format table

# Show detailed provider information
orb providers list --detailed --format yaml
```

### Provider Configuration Details

```bash
# Show specific provider configuration
orb --provider-name aws-prod providers show --format yaml

# Check provider health
orb --provider-name aws-prod providers health --detailed
```

### Debug Provider Selection

```bash
# Use verbose output to see provider selection
orb --verbose --provider-name aws-dev machines request template-id 1

# Check what provider would be selected
orb --dry-run --provider-name aws-prod machines request template-id 1
```

## Integration with Other Features

### MCP Server Mode

Provider override works with MCP server:

```bash
# Start MCP server with specific provider context
orb --provider-name aws-prod mcp serve --stdio
```

### API Server Mode

```bash
# Start API server with provider override
orb --provider-name aws-dev system serve --port 8000
```

### Batch Operations

```bash
# Process multiple requests with same provider
orb --provider-name aws-prod requests status req-123 req-456 req-789

# Return multiple machines from specific provider
orb --provider-name aws-prod machines return i-123 i-456 i-789
```

## See Also

- [CLI Reference](cli-reference.md) - Complete command reference
- [Multi-Provider Configuration](../configuration/multi-provider.md) - Provider configuration guide
- [Scheduler Commands](scheduler-commands.md) - Scheduler override functionality
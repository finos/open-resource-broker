# Provider Naming Convention Guide

This guide explains the provider naming conventions used in the Open Resource Broker system, providing clear patterns for consistent provider identification across multi-provider environments.

## Overview

Provider names follow structured patterns that encode key information about the provider configuration, making it easy to identify and manage providers in complex multi-provider setups.

## AWS Provider Naming Pattern

### Standard Pattern: `aws_{profile}_{region}`

AWS providers use a three-part naming convention:

1. **Type**: Always `aws` for AWS providers
2. **Profile**: AWS credential profile name
3. **Region**: AWS region identifier

### Examples

**Basic Configurations:**
- `aws_default_us-east-1` - Default profile in US East 1
- `aws_default_us-west-2` - Default profile in US West 2
- `aws_default_eu-west-1` - Default profile in EU West 1

**Environment-Specific Profiles:**
- `aws_prod_us-east-1` - Production profile in US East 1
- `aws_dev_us-west-2` - Development profile in US West 2
- `aws_staging_eu-west-1` - Staging profile in EU West 1

**Multi-Account Configurations:**
- `aws_account-prod_us-east-1` - Production account in US East 1
- `aws_account-dev_us-east-1` - Development account in US East 1
- `aws_shared-services_us-east-1` - Shared services account

## Configuration Examples

### Single Provider
```json
{
  "provider": {
    "selection_policy": "FIRST_AVAILABLE",
    "providers": [
      {
        "name": "aws_default_us-east-1",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "default"
        }
      }
    ]
  }
}
```

### Multi-Region Setup
```json
{
  "provider": {
    "selection_policy": "ROUND_ROBIN",
    "providers": [
      {
        "name": "aws_prod_us-east-1",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "prod"
        }
      },
      {
        "name": "aws_prod_us-west-2",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-west-2",
          "profile": "prod"
        }
      },
      {
        "name": "aws_prod_eu-west-1",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "eu-west-1",
          "profile": "prod"
        }
      }
    ]
  }
}
```

### Multi-Environment Setup
```json
{
  "provider": {
    "selection_policy": "WEIGHTED_ROUND_ROBIN",
    "providers": [
      {
        "name": "aws_prod_us-east-1",
        "type": "aws",
        "enabled": true,
        "weight": 70,
        "config": {
          "region": "us-east-1",
          "profile": "prod"
        }
      },
      {
        "name": "aws_dev_us-east-1",
        "type": "aws",
        "enabled": true,
        "weight": 20,
        "config": {
          "region": "us-east-1",
          "profile": "dev"
        }
      },
      {
        "name": "aws_staging_us-east-1",
        "type": "aws",
        "enabled": true,
        "weight": 10,
        "config": {
          "region": "us-east-1",
          "profile": "staging"
        }
      }
    ]
  }
}
```

## Template File Naming

Template files are automatically named based on provider names, ensuring clear association between providers and their templates.

### Naming Pattern: `{provider_name}_templates.json`

**Examples:**
- `aws_default_us-east-1_templates.json`
- `aws_prod_us-west-2_templates.json`
- `aws_dev_eu-west-1_templates.json`
- `aws_account-prod_us-east-1_templates.json`

### Template Generation Commands

```bash
# Generate templates for all active providers
orb templates generate

# Generate templates for specific provider
orb templates generate --provider aws_prod_us-east-1

# Generate templates for specific provider API
orb templates generate --provider-api EC2Fleet

# Generate templates for all providers of a specific type
orb templates generate --provider-type aws
```

## Provider Name Components

### Profile Component

The profile component identifies the AWS credential profile or environment:

**Standard Profiles:**
- `default` - Default AWS profile
- `prod` - Production environment
- `dev` - Development environment
- `staging` - Staging environment
- `test` - Testing environment

**Account-Based Profiles:**
- `account-prod` - Production account
- `account-dev` - Development account
- `shared-services` - Shared services account
- `security` - Security account

### Region Component

The region component uses standard AWS region identifiers:

**US Regions:**
- `us-east-1` - US East (N. Virginia)
- `us-east-2` - US East (Ohio)
- `us-west-1` - US West (N. California)
- `us-west-2` - US West (Oregon)

**EU Regions:**
- `eu-west-1` - Europe (Ireland)
- `eu-west-2` - Europe (London)
- `eu-west-3` - Europe (Paris)
- `eu-central-1` - Europe (Frankfurt)

**Asia Pacific Regions:**
- `ap-southeast-1` - Asia Pacific (Singapore)
- `ap-southeast-2` - Asia Pacific (Sydney)
- `ap-northeast-1` - Asia Pacific (Tokyo)
- `ap-south-1` - Asia Pacific (Mumbai)

## Best Practices

### Naming Conventions

1. **Use Descriptive Profiles**: Choose profile names that clearly indicate the environment or purpose
2. **Consistent Naming**: Use consistent naming patterns across all environments
3. **Avoid Special Characters**: Stick to alphanumeric characters, hyphens, and underscores
4. **Environment Prefixes**: Use clear environment prefixes (prod, dev, staging, test)

### Configuration Management

1. **Environment Separation**: Use different profiles for different environments
2. **Region Strategy**: Plan your region strategy based on latency, compliance, and disaster recovery needs
3. **Account Isolation**: Use separate AWS accounts for different environments when possible
4. **Template Organization**: Keep templates organized by provider name for easy management

### Migration Strategy

When migrating from old naming conventions:

1. **Gradual Migration**: Migrate one provider at a time
2. **Backup Configurations**: Always backup existing configurations before migration
3. **Test Thoroughly**: Test each migrated provider before enabling in production
4. **Update Documentation**: Update all documentation to reflect new naming conventions

## CLI Integration

### Provider Override

Use the `--provider` flag to specify a particular provider:

```bash
# Use specific provider for template operations
orb templates list --provider aws_prod_us-east-1

# Use specific provider for machine requests
orb machines request template-id 5 --provider aws_dev_us-west-2

# Check health of specific provider
orb system health --provider aws_staging_eu-west-1
```

### Provider Discovery

List and discover providers using the new naming:

```bash
# List all providers
orb providers list

# Show provider configuration
orb providers show aws_prod_us-east-1

# Check provider health
orb providers health aws_prod_us-east-1
```

## Troubleshooting

### Common Issues

**Provider Not Found:**
- Verify the provider name matches the configuration exactly
- Check that the provider is enabled in the configuration
- Ensure the provider name follows the correct pattern

**Template File Not Found:**
- Verify the template file exists with the correct naming pattern
- Check that templates have been generated for the provider
- Ensure the provider name in the template filename matches the configuration

**Configuration Validation Errors:**
- Verify that profile and region in the provider name match the configuration
- Check that all required configuration fields are present
- Ensure the provider type is correctly set to "aws"

### Debug Commands

```bash
# Validate provider configuration
orb config validate --provider aws_prod_us-east-1

# Show detailed provider information
orb providers show aws_prod_us-east-1 --detailed

# Test provider connectivity
orb providers test aws_prod_us-east-1
```

## Future Extensions

The naming convention is designed to be extensible for future provider types:

**Potential Future Patterns:**
- `provider1_{account}_{region}` - Provider1 providers
- `provider2_{project}_{region}` - Provider2 providers

This structured approach ensures consistency and scalability as new provider types are added to the system.
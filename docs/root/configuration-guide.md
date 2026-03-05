# Configuration-Driven Provider Strategy Guide

This guide covers the configuration-driven provider strategy system implemented in Phases 1-3, providing comprehensive instructions for configuring, managing, and operating multi-provider environments.

## Table of Contents

1. [Overview](#overview)
2. [Provider Naming Conventions](#provider-naming-conventions)
3. [Configuration Formats](#configuration-formats)
4. [Provider Modes](#provider-modes)
5. [Configuration Management](#configuration-management)
6. [CLI Operations](#cli-operations)
7. [Migration Guide](#migration-guide)
8. [Troubleshooting](#troubleshooting)

## Overview

The configuration-driven provider strategy system enables declarative management of cloud providers through consolidated configuration files. The system supports three operational modes:

- **Single Provider Mode**: One active provider with simple configuration
- **Multi-Provider Mode**: Multiple providers with load balancing and failover
- **Legacy Mode**: Backward compatibility with existing AWS-only configuration

## Provider Naming Conventions

### AWS Provider Naming Pattern

AWS providers follow the pattern: `aws_{profile}_{region}`

**Examples:**
- `aws_default_us-east-1` - Default profile in US East 1
- `aws_prod_us-west-2` - Production profile in US West 2
- `aws_dev_eu-west-1` - Development profile in EU West 1

**Components:**
- **Type**: Always `aws` for AWS providers
- **Profile**: AWS credential profile name (e.g., `default`, `prod`, `dev`)
- **Region**: AWS region identifier (e.g., `us-east-1`, `us-west-2`, `eu-west-1`)

### Provider Name Generation

The system automatically generates provider names based on configuration:

```json
{
  "name": "aws_prod_us-east-1",
  "type": "aws",
  "config": {
    "profile": "prod",
    "region": "us-east-1"
  }
}
```

### Template File Naming

Template files are named based on provider names:
- `aws_default_us-east-1_templates.json`
- `aws_prod_us-west-2_templates.json`
- `aws_dev_eu-west-1_templates.json`

### Multi-Provider Template Generation

Generate templates for multiple providers:

```bash
# Generate for all active providers
orb templates generate

# Generate for specific provider
orb templates generate --provider aws_prod_us-east-1

# Generate for specific provider type
orb templates generate --provider-api EC2Fleet
```

## Configuration Formats

### Consolidated Configuration Format (Recommended)

The consolidated configuration format provides comprehensive provider management:

```json
{
  "provider": {
    "selection_policy": "ROUND_ROBIN",
    "active_provider": "aws-primary",
    "health_check_interval": 30,
    "circuit_breaker": {
      "enabled": true,
      "failure_threshold": 5,
      "recovery_timeout": 60,
      "half_open_max_calls": 3
    },
    "providers": [
      {
        "name": "aws_default_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 70,
        "capabilities": ["compute", "storage", "networking"],
        "config": {
          "region": "us-east-1",
          "profile": "default",
          "max_retries": 3,
          "timeout": 30
        }
      },
      {
        "name": "aws_backup_us-west-2",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 30,
        "capabilities": ["compute", "storage"],
        "config": {
          "region": "us-west-2",
          "profile": "backup",
          "max_retries": 5,
          "timeout": 45
        }
      }
    ]
  },
  "logging": {
    "level": "INFO",
    "file_path": "logs/app.log",
    "console_enabled": true
  },
  "storage": {
    "strategy": "json",
    "json_strategy": {
      "storage_type": "single_file",
      "base_path": "data",
      "filenames": {
        "single_file": "request_database.json"
      }
    }
  },
  "scheduler": {
    "strategy": "hostfactory",
    "config_root": "config",
    "template_path": "awsprov_templates.json",
    "field_mapping": {
      "template_id_field": "templateId",
      "max_instances_field": "maxNumber",
      "image_id_field": "imageId",
      "instance_type_field": "vmType"
    },
    "output_format": {
      "use_camel_case": true,
      "include_attributes": true,
      "attribute_format": "hostfactory"
    }
  },
  "template": {
    "default_image_id": "ami-12345678",
    "default_instance_type": "t2.micro",
    "subnet_ids": ["subnet-12345678"],
    "security_group_ids": ["sg-12345678"],
    "ami_resolution": {
      "enabled": true,
      "fallback_on_failure": true,
      "cache_enabled": true
    }
  },
  "aws": {
    "launch_template": {
      "create_per_request": true,
      "naming_strategy": "request_based",
      "version_strategy": "incremental",
      "reuse_existing": true,
      "cleanup_old_versions": false,
      "max_versions_per_template": 10
    }
  }
}
```

### Legacy Configuration Format (Supported)

The system maintains backward compatibility with existing AWS configuration:

```json
{
  "provider": {
    "type": "aws",
    "aws": {
      "region": "us-east-1",
      "profile": "default"
    }
  },
  "logging": {
    "level": "INFO"
  }
}
```

## Provider Modes

### Single Provider Mode

Activated when:
- One provider is enabled in the configuration
- `active_provider` is specified with one provider
- Legacy configuration is used

**Characteristics:**
- Simple configuration and operation
- No load balancing or failover
- Direct provider communication
- Suitable for development and simple deployments

**Example Configuration:**
```json
{
  "provider": {
    "active_provider": "aws_default_us-east-1",
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

### Multi-Provider Mode

Activated when:
- Multiple providers are enabled
- Selection policy is configured
- No single `active_provider` is specified

**Characteristics:**
- Load balancing across providers
- Automatic failover on provider failure
- Health monitoring and circuit breaker protection
- Advanced selection policies

**Selection Policies:**
- `FIRST_AVAILABLE`: Use first healthy provider
- `ROUND_ROBIN`: Rotate between providers evenly
- `WEIGHTED_ROUND_ROBIN`: Rotate based on provider weights
- `LEAST_CONNECTIONS`: Use provider with fewest active connections
- `FASTEST_RESPONSE`: Use provider with best response time
- `HIGHEST_SUCCESS_RATE`: Use provider with best success rate
- `CAPABILITY_BASED`: Select based on required capabilities
- `HEALTH_BASED`: Prefer healthiest providers
- `RANDOM`: Random provider selection

**Example Configuration:**
```json
{
  "provider": {
    "selection_policy": "WEIGHTED_ROUND_ROBIN",
    "health_check_interval": 30,
    "providers": [
      {
        "name": "aws_default_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 70,
        "config": {
          "region": "us-east-1",
          "profile": "default"
        }
      },
      {
        "name": "aws_default_us-west-2",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 30,
        "config": {
          "region": "us-west-2",
          "profile": "default"
        }
      }
    ]
  }
}
```

### Legacy Mode

Automatically activated when:
- Old configuration format is detected
- No consolidated provider configuration is available
- Backward compatibility is required

**Characteristics:**
- Full backward compatibility
- Single AWS provider operation
- Existing functionality preserved
- Automatic migration available

## Configuration Management

### Environment Variables

The Open Resource Broker provides comprehensive environment variable support using Pydantic BaseSettings for automatic type conversion, validation, and configuration management.

#### Environment Variable Naming Convention

Environment variables follow a hierarchical naming pattern:
- **Core settings**: `ORB_<FIELD_NAME>`
- **AWS provider**: `ORB_AWS_<FIELD_NAME>`
- **Nested objects**: `ORB_<SECTION>__<FIELD_NAME>` (double underscore)

#### Core Application Variables

```bash
# Application behavior
ORB_LOG_LEVEL=DEBUG                    # Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL
ORB_DEBUG=true                         # Enable debug mode (boolean)
ORB_ENVIRONMENT=production             # Environment identifier (string)
ORB_REQUEST_TIMEOUT=600                # Global request timeout in seconds (integer)
ORB_MAX_MACHINES_PER_REQUEST=200       # Maximum machines per single request (integer)

# Directory paths (standard across all schedulers)
ORB_CONFIG_DIR=/opt/orb/config         # Configuration files directory
ORB_WORK_DIR=/opt/orb/work             # Working directory for temporary files
ORB_LOG_DIR=/opt/orb/logs              # Log files directory
```

#### AWS Provider Variables

```bash
# Authentication and region
ORB_AWS_REGION=us-west-2               # AWS region (string)
ORB_AWS_PROFILE=production             # AWS credential profile name (string)
ORB_AWS_ROLE_ARN=arn:aws:iam::123456789012:role/OrbitRole  # IAM role ARN (string)
ORB_AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE                 # AWS access key ID (string)
ORB_AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY  # AWS secret access key (string)
ORB_AWS_SESSION_TOKEN=AQoEXAMPLEH4aoAH0gNCAPyJxz4ARKDHxyP5XpAa  # Session token (string)
ORB_AWS_ENDPOINT_URL=https://ec2.us-west-2.amazonaws.com   # Custom endpoint URL (string)

# Infrastructure defaults (from infrastructure discovery)
ORB_AWS_SUBNET_IDS='["subnet-12345678", "subnet-87654321"]'           # JSON array of subnet IDs
ORB_AWS_SECURITY_GROUP_IDS='["sg-abcdef12", "sg-34567890"]'           # JSON array of security group IDs
ORB_AWS_KEY_NAME=my-production-key     # EC2 key pair name (string)
ORB_AWS_IMAGE_ID=ami-0abcdef1234567890  # Default AMI ID (string)
ORB_AWS_INSTANCE_TYPE=t3.medium        # Default instance type (string)

# AWS service configuration
ORB_AWS_MAX_RETRIES=5                  # Maximum API retry attempts (integer, 0-10)
ORB_AWS_TIMEOUT=120                    # AWS API timeout in seconds (integer, 1-300)
```

#### Nested Configuration Variables

For complex configuration objects, use double underscores (`__`) to separate levels:

```bash
# Circuit breaker configuration
ORB_CIRCUIT_BREAKER__ENABLED=true                    # Enable circuit breaker (boolean)
ORB_CIRCUIT_BREAKER__FAILURE_THRESHOLD=10            # Failures before opening circuit (integer)
ORB_CIRCUIT_BREAKER__RECOVERY_TIMEOUT=120            # Recovery timeout in seconds (integer)
ORB_CIRCUIT_BREAKER__HALF_OPEN_MAX_CALLS=3           # Max calls in half-open state (integer)

# Retry configuration
ORB_RETRY__MAX_ATTEMPTS=5              # Maximum retry attempts (integer)
ORB_RETRY__BACKOFF_MULTIPLIER=2.0      # Exponential backoff multiplier (float)
ORB_RETRY__MAX_DELAY=60                # Maximum delay between retries in seconds (integer)
```

#### Scheduler-Specific Variables (Legacy Support)

For backward compatibility with existing HostFactory deployments:

```bash
# HostFactory scheduler (legacy)
HF_PROVIDER_WORKDIR=/var/lib/hostfactory/work        # HostFactory working directory
HF_PROVIDER_CONFDIR=/etc/hostfactory/config          # HostFactory configuration directory
HF_PROVIDER_LOGDIR=/var/log/hostfactory              # HostFactory log directory
HF_LOGLEVEL=DEBUG                                    # HostFactory-specific log level
HF_LOGGING_CONSOLE_ENABLED=false                     # Disable console output for JSON-only mode

# Default scheduler
DEFAULT_PROVIDER_WORKDIR=/opt/orb/work               # Default scheduler working directory
DEFAULT_PROVIDER_CONFDIR=/opt/orb/config             # Default scheduler configuration directory
DEFAULT_PROVIDER_LOGDIR=/opt/orb/logs                # Default scheduler log directory
```

#### Configuration Precedence

Environment variables take precedence over configuration files:

1. **Environment Variables** (highest precedence)
2. **Configuration File** (`config.json`)
3. **Provider Defaults** (from template_defaults)
4. **System Defaults** (lowest precedence)

#### Example: Production Environment Setup

```bash
#!/bin/bash
# Production environment configuration
export ORB_ENVIRONMENT=production
export ORB_LOG_LEVEL=INFO
export ORB_DEBUG=false

# AWS production account with IAM role
export ORB_AWS_REGION=us-east-1
export ORB_AWS_ROLE_ARN=arn:aws:iam::123456789012:role/OrbitProductionRole
export ORB_AWS_SUBNET_IDS='["subnet-prod123", "subnet-prod456"]'
export ORB_AWS_SECURITY_GROUP_IDS='["sg-prod123"]'
export ORB_AWS_KEY_NAME=production-key-pair

# Performance and resilience settings
export ORB_REQUEST_TIMEOUT=300
export ORB_MAX_MACHINES_PER_REQUEST=100
export ORB_AWS_MAX_RETRIES=5
export ORB_CIRCUIT_BREAKER__ENABLED=true
export ORB_CIRCUIT_BREAKER__FAILURE_THRESHOLD=5

# Start the application
orb system serve --port 8000
```

### Configuration Validation

Validate your configuration before deployment:

```bash
# Validate current configuration
orb config validate

# Validate specific configuration file
orb config validate --config config/production.json
```

### Configuration Reload

Reload configuration without restarting the application:

```bash
# Reload from default location
orb config show

# Reload from specific file
orb --config config/new-config.json config show
```

## CLI Operations

### Provider Configuration Operations

```bash
# Get current provider configuration
orb config show

# Get provider configuration with sensitive data
orb config show

# Validate provider configuration
orb config validate

# Validate with detailed output
orb config validate

# Reload provider configuration
orb --config config/updated.json config show

# Migrate legacy configuration (manual: update config.json to consolidated format)
```

### Provider Strategy Operations

```bash
# Select provider strategy for operation
orb providers select --data '{
  "operation_type": "CREATE_INSTANCES",
  "required_capabilities": ["compute"],
  "min_success_rate": 0.95,
  "require_healthy": true
}'

# Execute provider operation
orb providers exec --data '{
  "operation_type": "CREATE_INSTANCES",
  "operation_data": {
    "instance_count": 2,
    "instance_type": "t2.micro"
  }
}'

# Get provider health status
orb providers health

# List available providers
orb providers list
```

### Template Operations

```bash
# Get available templates
orb templates list

# Get templates for specific provider
orb templates list --provider aws-primary

# Request machines
orb machines request basic-template 2
```

## Migration Guide

### Migrating from Legacy Configuration

#### Backup Current Configuration

```bash
# Create backup of current configuration
cp config/awsprov_config.json config/awsprov_config.json.backup
cp config/awsprov_templates.json config/awsprov_templates.json.backup
```

#### Run Migration Tool

```bash
# Migrate to consolidated format with backup
# Update config.json manually to the consolidated format shown above.
# Back up your original first:
cp config/awsprov_config.json config/awsprov_config.json.backup
```

#### Validate Migrated Configuration

```bash
# Validate the migrated configuration
orb config validate

# Test provider operations
orb config show
orb templates list
```

#### Update Deployment Scripts

Update your deployment scripts to use the new configuration format:

```bash
# Old way
export AWS_REGION=us-east-1
export AWS_PROFILE=default

# New way (optional, configuration file preferred)
export HF_PROVIDER_SELECTION_POLICY=FIRST_AVAILABLE
export HF_PROVIDER_HEALTH_CHECK_INTERVAL=30
```

### Migration Scenarios

#### Single AWS Region to Multi-Region

**Before (Legacy):**
```json
{
  "provider": {
    "type": "aws",
    "aws": {
      "region": "us-east-1",
      "profile": "default"
    }
  }
}
```

**After (Consolidated):**
```json
{
  "provider": {
    "selection_policy": "ROUND_ROBIN",
    "providers": [
      {
        "name": "aws_default_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 50,
        "config": {
          "region": "us-east-1",
          "profile": "default"
        }
      },
      {
        "name": "aws_default_us-west-2",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 50,
        "config": {
          "region": "us-west-2",
          "profile": "default"
        }
      }
    ]
  }
}
```

#### Adding Provider Redundancy

**Before (Single Provider):**
```json
{
  "provider": {
    "active_provider": "aws_default_us-east-1",
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

**After (Multi-Provider with Failover):**
```json
{
  "provider": {
    "selection_policy": "HEALTH_BASED",
    "health_check_interval": 30,
    "circuit_breaker": {
      "enabled": true,
      "failure_threshold": 3,
      "recovery_timeout": 60
    },
    "providers": [
      {
        "name": "aws_default_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 80,
        "config": {
          "region": "us-east-1",
          "profile": "default"
        }
      },
      {
        "name": "aws_default_us-west-2",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 20,
        "config": {
          "region": "us-west-2",
          "profile": "default"
        }
      }
    ]
  }
}
```

## Troubleshooting

### Common Issues

#### Configuration Validation Errors

**Problem:** Configuration validation fails with provider errors.

**Solution:**
```bash
# Get detailed validation information
orb config validate

# Check provider configuration
orb config show
```

#### Provider Selection Issues

**Problem:** Provider selection not working as expected.

**Diagnosis:**
```bash
# Check provider health
orb providers health

# List available providers
orb providers list

# Test provider selection
orb providers select --data '{
  "operation_type": "CREATE_INSTANCES",
  "required_capabilities": ["compute"]
}'
```

#### Migration Problems

**Problem:** Configuration migration fails or produces unexpected results.

**Solution:**
```bash
# Validate original configuration first
orb config validate

# Run migration with backup (manual: back up config file first)
cp config/awsprov_config.json config/awsprov_config.json.backup
```

### Debug Mode

Enable debug logging for detailed troubleshooting:

```json
{
  "logging": {
    "level": "DEBUG",
    "console_enabled": true,
    "file_path": "logs/debug.log"
  }
}
```

Or via environment variable:
```bash
export HF_LOGGING_LEVEL=DEBUG
```

### Health Checks

Monitor provider health and system status:

```bash
# Check overall system health
orb system health

# Get provider-specific health
orb providers health

# Monitor provider metrics
orb providers metrics
```

### Performance Tuning

Optimize performance for your environment:

```json
{
  "provider": {
    "health_check_interval": 60,
    "circuit_breaker": {
      "failure_threshold": 5,
      "recovery_timeout": 120
    }
  },
  "template": {
    "ami_resolution": {
      "cache_enabled": true
    }
  }
}
```

## Best Practices

### Configuration Management

1. **Use Version Control**: Store configuration files in version control
2. **Environment-Specific Configs**: Maintain separate configs for dev/staging/prod
3. **Validate Before Deploy**: Always validate configuration before deployment
4. **Monitor Health**: Set up monitoring for provider health and performance
5. **Regular Backups**: Backup configuration files before changes

### Provider Strategy

1. **Start Simple**: Begin with single provider mode, expand to multi-provider as needed
2. **Test Failover**: Regularly test provider failover scenarios
3. **Monitor Performance**: Track provider response times and success rates
4. **Capacity Planning**: Configure provider weights based on capacity
5. **Health Thresholds**: Set appropriate health check intervals and thresholds

### Security

1. **Credential Management**: Use IAM roles and profiles instead of hardcoded credentials
2. **Network Security**: Configure appropriate security groups and network access
3. **Audit Logging**: Enable comprehensive logging for audit trails
4. **Access Control**: Implement appropriate access controls for configuration files
5. **Regular Updates**: Keep provider configurations and credentials updated

This guide provides comprehensive coverage of the configuration-driven provider strategy system. For additional examples and advanced configurations, see the examples directory.

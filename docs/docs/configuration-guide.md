# Configuration-Driven Provider Strategy Guide

This guide covers the configuration-driven provider strategy system implemented in Phases 1-3, providing comprehensive instructions for configuring, managing, and operating multi-provider environments.

## Table of Contents

1. [Overview](#overview)
2. [Configuration Formats](#configuration-formats)
3. [Provider Modes](#provider-modes)
4. [Configuration Management](#configuration-management)
5. [CLI Operations](#cli-operations)
6. [Migration Guide](#migration-guide)
7. [Troubleshooting](#troubleshooting)

## Overview

The configuration-driven provider strategy system enables declarative management of cloud providers through consolidated configuration files. The system supports three operational modes:

- **Single Provider Mode**: One active provider with simple configuration
- **Multi-Provider Mode**: Multiple providers with load balancing and failover
- **Legacy Mode**: Backward compatibility with existing AWS-only configuration

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
        "name": "aws-primary",
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
        "name": "aws-backup",
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
    "active_provider": "aws-primary",
    "providers": [
      {
        "name": "aws-primary",
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
        "name": "aws-primary",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 70,
        "config": {
          "region": "us-east-1"
        }
      },
      {
        "name": "aws-backup",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 30,
        "config": {
          "region": "us-west-2"
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

Override configuration values using environment variables:

```bash
# Provider configuration
export HF_PROVIDER_SELECTION_POLICY=ROUND_ROBIN
export HF_PROVIDER_HEALTH_CHECK_INTERVAL=60

# Template configuration
export HF_TEMPLATE_AMI_RESOLUTION_ENABLED=true
export HF_TEMPLATE_AMI_RESOLUTION_CACHE_ENABLED=true

# Logging configuration
export HF_LOGGING_LEVEL=DEBUG
export HF_LOGGING_CONSOLE_ENABLED=true

# Storage configuration
export HF_STORAGE_STRATEGY=json

# Scheduler configuration
export HF_SCHEDULER_STRATEGY=hostfactory
export HF_SCHEDULER_CONFIG_ROOT=config
export HF_SCHEDULER_TEMPLATE_PATH=awsprov_templates.json
```

### Configuration Validation

Validate your configuration before deployment:

```bash
# Validate current configuration
python run.py validateProviderConfig

# Validate specific configuration file
python run.py validateProviderConfig --file config/production.json
```

### Configuration Reload

Reload configuration without restarting the application:

```bash
# Reload from default location
python run.py reloadProviderConfig

# Reload from specific file
python run.py reloadProviderConfig --config-path config/new-config.json
```

## CLI Operations

### Provider Configuration Operations

```bash
# Get current provider configuration
python run.py getProviderConfig

# Get provider configuration with sensitive data
python run.py getProviderConfig --data '{"include_sensitive": true}'

# Validate provider configuration
python run.py validateProviderConfig

# Validate with detailed output
python run.py validateProviderConfig --data '{"detailed": true}'

# Reload provider configuration
python run.py reloadProviderConfig --config-path config/updated.json

# Migrate legacy configuration
python run.py migrateProviderConfig --data '{"save_to_file": true, "backup_original": true}'
```

### Provider Strategy Operations

```bash
# Select provider strategy for operation
python run.py selectProviderStrategy --data '{
  "operation_type": "CREATE_INSTANCES",
  "required_capabilities": ["compute"],
  "min_success_rate": 0.95,
  "require_healthy": true
}'

# Execute provider operation
python run.py executeProviderOperation --data '{
  "operation_type": "CREATE_INSTANCES",
  "operation_data": {
    "instance_count": 2,
    "instance_type": "t2.micro"
  }
}'

# Get provider health status
python run.py getProviderHealth

# List available providers
python run.py listAvailableProviders
```

### Template Operations

```bash
# Get available templates (now with provider strategy support)
python run.py getAvailableTemplates

# Get templates for specific provider
python run.py getAvailableTemplates --provider-api aws-primary

# Request machines (now with provider selection)
python run.py requestMachines --data '{
  "template_id": "basic-template",
  "machine_count": 2,
  "provider_preference": "aws-primary"
}'
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
python run.py migrateProviderConfig --data '{
  "save_to_file": true,
  "backup_original": true
}'
```

#### Validate Migrated Configuration

```bash
# Validate the migrated configuration
python run.py validateProviderConfig

# Test provider operations
python run.py getProviderConfig
python run.py getAvailableTemplates
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
        "name": "aws-east",
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
        "name": "aws-west",
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
    "active_provider": "aws-primary",
    "providers": [
      {
        "name": "aws-primary",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1"
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
        "name": "aws-primary",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 80,
        "config": {
          "region": "us-east-1"
        }
      },
      {
        "name": "aws-backup",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 20,
        "config": {
          "region": "us-west-2"
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
python run.py validateProviderConfig --data '{"detailed": true}'

# Check provider configuration
python run.py getProviderConfig
```

#### Provider Selection Issues

**Problem:** Provider selection not working as expected.

**Diagnosis:**
```bash
# Check provider health
python run.py getProviderHealth

# List available providers
python run.py listAvailableProviders

# Test provider selection
python run.py selectProviderStrategy --data '{
  "operation_type": "CREATE_INSTANCES",
  "required_capabilities": ["compute"]
}'
```

#### Migration Problems

**Problem:** Configuration migration fails or produces unexpected results.

**Solution:**
```bash
# Validate original configuration first
python run.py validateProviderConfig

# Run migration with backup
python run.py migrateProviderConfig --data '{
  "save_to_file": false,
  "backup_original": true
}'

# Review migration output before saving
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
python run.py healthCheck

# Get provider-specific health
python run.py getProviderHealth

# Monitor provider metrics
python run.py getProviderMetrics
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
4. **Access Control**: Implement proper access controls for configuration files
5. **Regular Updates**: Keep provider configurations and credentials updated

This guide provides comprehensive coverage of the configuration-driven provider strategy system. For additional examples and advanced configurations, see the examples directory.

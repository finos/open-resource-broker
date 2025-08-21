# Native Spec Configuration Guide

## Overview

This guide covers all configuration options for native AWS spec support, from basic enablement to advanced performance tuning and security settings.

## Basic Configuration

### Minimal Configuration

To enable native spec support with default settings:

```json
{
  "native_spec": {
    "enabled": true
  }
}
```

### Standard Configuration

Recommended configuration for most use cases:

```json
{
  "native_spec": {
    "enabled": true,
    "merge_mode": "extend"
  }
}
```

## Complete Configuration Schema

### Native Spec Configuration

```json
{
  "native_spec": {
    "enabled": true,
    "merge_mode": "extend",
    "validation": {
      "strict_mode": true,
      "allow_unknown_variables": false,
      "validate_aws_schemas": true,
      "max_template_size_kb": 1024
    },
    "rendering": {
      "template_cache_size": 100,
      "template_timeout_seconds": 30,
      "max_recursion_depth": 10,
      "enable_auto_escape": true
    },
    "error_handling": {
      "fallback_to_legacy": true,
      "log_rendering_errors": true,
      "fail_fast_on_errors": false
    }
  }
}
```

### Provider-Specific Configuration

```json
{
  "provider": {
    "provider_defaults": {
      "aws": {
        "extensions": {
          "native_spec": {
            "spec_file_base_path": "specs/aws",
            "template_search_paths": [
              "specs/aws/templates",
              "specs/aws/examples",
              "specs/aws/production"
            ],
            "allowed_file_extensions": [".json", ".yaml", ".yml"],
            "max_file_size_mb": 10,
            "enable_file_watching": true
          }
        }
      }
    }
  }
}
```

## Configuration Options Reference

### Core Settings

#### `enabled`
- **Type**: `boolean`
- **Default**: `false`
- **Description**: Enable/disable native spec processing
- **Environment Variable**: `NATIVE_SPEC_ENABLED`

```json
{
  "native_spec": {
    "enabled": true
  }
}
```

#### `merge_mode`
- **Type**: `string`
- **Options**: `"extend"`, `"override"`, `"none"`
- **Default**: `"extend"`
- **Description**: How native specs interact with legacy template fields

**Merge Mode Options:**

- **`extend`**: Native specs extend legacy template configuration
  - Legacy fields provide base configuration
  - Native specs add or override specific fields
  - Recommended for gradual migration

- **`override`**: Native specs completely replace legacy logic
  - Legacy template fields ignored when native specs present
  - Full control over AWS API parameters
  - Recommended for new templates

- **`none`**: Disable native spec processing
  - All templates use legacy processing
  - Useful for troubleshooting or rollback

```json
{
  "native_spec": {
    "merge_mode": "extend"
  }
}
```

### Validation Settings

#### `validation.strict_mode`
- **Type**: `boolean`
- **Default**: `true`
- **Description**: Enable strict validation of template syntax and AWS schemas

#### `validation.allow_unknown_variables`
- **Type**: `boolean`
- **Default**: `false`
- **Description**: Allow undefined variables in templates (renders as empty string)

#### `validation.validate_aws_schemas`
- **Type**: `boolean`
- **Default**: `true`
- **Description**: Validate rendered specs against AWS API schemas

#### `validation.max_template_size_kb`
- **Type**: `integer`
- **Default**: `1024`
- **Description**: Maximum template file size in KB

```json
{
  "native_spec": {
    "validation": {
      "strict_mode": true,
      "allow_unknown_variables": false,
      "validate_aws_schemas": true,
      "max_template_size_kb": 2048
    }
  }
}
```

### Rendering Settings

#### `rendering.template_cache_size`
- **Type**: `integer`
- **Default**: `100`
- **Description**: Number of parsed templates to cache in memory

#### `rendering.template_timeout_seconds`
- **Type**: `integer`
- **Default**: `30`
- **Description**: Maximum time allowed for template rendering

#### `rendering.max_recursion_depth`
- **Type**: `integer`
- **Default**: `10`
- **Description**: Maximum depth for nested template includes

#### `rendering.enable_auto_escape`
- **Type**: `boolean`
- **Default**: `true`
- **Description**: Automatically escape HTML/XML characters in variables

```json
{
  "native_spec": {
    "rendering": {
      "template_cache_size": 200,
      "template_timeout_seconds": 60,
      "max_recursion_depth": 15,
      "enable_auto_escape": false
    }
  }
}
```

### Error Handling Settings

#### `error_handling.fallback_to_legacy`
- **Type**: `boolean`
- **Default**: `true`
- **Description**: Fall back to legacy processing if native spec fails

#### `error_handling.log_rendering_errors`
- **Type**: `boolean`
- **Default**: `true`
- **Description**: Log detailed error information for template rendering failures

#### `error_handling.fail_fast_on_errors`
- **Type**: `boolean`
- **Default**: `false`
- **Description**: Fail immediately on first error instead of attempting fallback

```json
{
  "native_spec": {
    "error_handling": {
      "fallback_to_legacy": true,
      "log_rendering_errors": true,
      "fail_fast_on_errors": false
    }
  }
}
```

### File System Settings

#### `spec_file_base_path`
- **Type**: `string`
- **Default**: `"specs/aws"`
- **Description**: Base directory for spec template files
- **Environment Variable**: `NATIVE_SPEC_BASE_PATH`

#### `template_search_paths`
- **Type**: `array[string]`
- **Default**: `["specs/aws"]`
- **Description**: Additional directories to search for template files

#### `allowed_file_extensions`
- **Type**: `array[string]`
- **Default**: `[".json", ".yaml", ".yml"]`
- **Description**: Allowed file extensions for spec templates

#### `max_file_size_mb`
- **Type**: `integer`
- **Default**: `10`
- **Description**: Maximum spec file size in MB

#### `enable_file_watching`
- **Type**: `boolean`
- **Default**: `true`
- **Description**: Watch spec files for changes and invalidate cache

```json
{
  "provider": {
    "provider_defaults": {
      "aws": {
        "extensions": {
          "native_spec": {
            "spec_file_base_path": "/opt/hostfactory/specs/aws",
            "template_search_paths": [
              "/opt/hostfactory/specs/aws/production",
              "/opt/hostfactory/specs/aws/staging",
              "/opt/hostfactory/specs/aws/shared"
            ],
            "allowed_file_extensions": [".json", ".yaml"],
            "max_file_size_mb": 5,
            "enable_file_watching": false
          }
        }
      }
    }
  }
}
```

## Environment-Specific Configurations

### Development Environment

```json
{
  "native_spec": {
    "enabled": true,
    "merge_mode": "extend",
    "validation": {
      "strict_mode": false,
      "allow_unknown_variables": true,
      "validate_aws_schemas": false
    },
    "rendering": {
      "template_cache_size": 10,
      "template_timeout_seconds": 60
    },
    "error_handling": {
      "fallback_to_legacy": true,
      "log_rendering_errors": true,
      "fail_fast_on_errors": false
    }
  }
}
```

### Staging Environment

```json
{
  "native_spec": {
    "enabled": true,
    "merge_mode": "extend",
    "validation": {
      "strict_mode": true,
      "allow_unknown_variables": false,
      "validate_aws_schemas": true
    },
    "rendering": {
      "template_cache_size": 50,
      "template_timeout_seconds": 45
    },
    "error_handling": {
      "fallback_to_legacy": true,
      "log_rendering_errors": true,
      "fail_fast_on_errors": false
    }
  }
}
```

### Production Environment

```json
{
  "native_spec": {
    "enabled": true,
    "merge_mode": "override",
    "validation": {
      "strict_mode": true,
      "allow_unknown_variables": false,
      "validate_aws_schemas": true,
      "max_template_size_kb": 512
    },
    "rendering": {
      "template_cache_size": 200,
      "template_timeout_seconds": 30,
      "max_recursion_depth": 5,
      "enable_auto_escape": true
    },
    "error_handling": {
      "fallback_to_legacy": false,
      "log_rendering_errors": true,
      "fail_fast_on_errors": true
    }
  }
}
```

## Environment Variables

All configuration options can be overridden using environment variables:

### Core Settings
- `NATIVE_SPEC_ENABLED`: Override `native_spec.enabled`
- `NATIVE_SPEC_MERGE_MODE`: Override `native_spec.merge_mode`
- `NATIVE_SPEC_BASE_PATH`: Override `spec_file_base_path`

### Performance Settings
- `NATIVE_SPEC_CACHE_SIZE`: Override `template_cache_size`
- `NATIVE_SPEC_TIMEOUT`: Override `template_timeout_seconds`

### Validation Settings
- `NATIVE_SPEC_STRICT_MODE`: Override `validation.strict_mode`
- `NATIVE_SPEC_VALIDATE_SCHEMAS`: Override `validation.validate_aws_schemas`

### Example Environment Configuration

```bash
# Enable native specs with strict validation
export NATIVE_SPEC_ENABLED=true
export NATIVE_SPEC_MERGE_MODE=override
export NATIVE_SPEC_STRICT_MODE=true

# Performance tuning
export NATIVE_SPEC_CACHE_SIZE=500
export NATIVE_SPEC_TIMEOUT=60

# Custom spec file location
export NATIVE_SPEC_BASE_PATH=/opt/custom/specs/aws
```

## Configuration Validation

### Validate Configuration

Use the built-in validation tool to check your configuration:

```bash
# Validate current configuration
ohfp config validate --native-spec

# Validate specific configuration file
ohfp config validate --file config.json --native-spec

# Test template rendering with current config
ohfp templates validate --native-spec-test
```

### Common Configuration Issues

#### Issue: Templates not found
```
Error: Template file 'examples/basic/ec2fleet-instant.json' not found
```
**Solution**: Check `spec_file_base_path` and `template_search_paths` settings

#### Issue: Template rendering timeout
```
Error: Template rendering exceeded timeout of 30 seconds
```
**Solution**: Increase `template_timeout_seconds` or optimize template complexity

#### Issue: Cache memory usage
```
Warning: Template cache using excessive memory
```
**Solution**: Reduce `template_cache_size` or increase available memory

## Performance Tuning

### Cache Optimization

```json
{
  "native_spec": {
    "rendering": {
      "template_cache_size": 500,
      "enable_file_watching": false
    }
  }
}
```

### Memory Usage Guidelines

| Cache Size | Memory Usage | Recommended For |
|------------|--------------|-----------------|
| 10-50 | ~10-50 MB | Development |
| 100-200 | ~100-200 MB | Staging |
| 500-1000 | ~500MB-1GB | Production |

### Timeout Guidelines

| Environment | Recommended Timeout | Rationale |
|-------------|-------------------|-----------|
| Development | 60-120 seconds | Allow debugging |
| Staging | 30-60 seconds | Balance testing/performance |
| Production | 15-30 seconds | Fast failure detection |

## Security Configuration

### Secure File Access

```json
{
  "provider": {
    "provider_defaults": {
      "aws": {
        "extensions": {
          "native_spec": {
            "spec_file_base_path": "/opt/secure/specs",
            "allowed_file_extensions": [".json"],
            "max_file_size_mb": 1
          }
        }
      }
    }
  }
}
```

### Template Security

```json
{
  "native_spec": {
    "validation": {
      "strict_mode": true,
      "allow_unknown_variables": false,
      "max_template_size_kb": 256
    },
    "rendering": {
      "enable_auto_escape": true,
      "max_recursion_depth": 5
    }
  }
}
```

## Monitoring and Observability

### Metrics Configuration

Enable metrics collection for native spec operations:

```json
{
  "monitoring": {
    "native_spec_metrics": {
      "enabled": true,
      "include_template_names": false,
      "include_rendering_time": true,
      "include_cache_stats": true
    }
  }
}
```

### Log Configuration

Configure detailed logging for troubleshooting:

```json
{
  "logging": {
    "native_spec": {
      "level": "INFO",
      "include_template_content": false,
      "include_variable_values": false,
      "log_cache_operations": true
    }
  }
}
```

## Troubleshooting

### Enable Debug Logging

```json
{
  "logging": {
    "native_spec": {
      "level": "DEBUG",
      "include_template_content": true,
      "include_variable_values": true
    }
  }
}
```

### Disable Native Specs Temporarily

```bash
# Quick disable via environment variable
export NATIVE_SPEC_ENABLED=false

# Or update configuration
{
  "native_spec": {
    "enabled": false
  }
}
```

### Test Configuration Changes

```bash
# Test configuration without applying
ohfp config test --native-spec

# Validate specific template with new config
ohfp templates render --template-id test-template --dry-run
```

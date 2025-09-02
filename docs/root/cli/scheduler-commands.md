# Scheduler Management Commands

The scheduler management commands allow you to list, configure, and validate scheduler strategies in the Open Host Factory Plugin.

## Available Commands

### List Scheduler Strategies

List all available scheduler strategies:

```bash
ohfp scheduler list
```

List with detailed information:

```bash
ohfp scheduler list --long
```

Output formats:

```bash
ohfp scheduler list --format table
ohfp scheduler list --format yaml
ohfp scheduler list --format json
```

### Show Scheduler Configuration

Show current scheduler configuration:

```bash
ohfp scheduler show
```

Show specific scheduler configuration:

```bash
ohfp scheduler show --scheduler default
ohfp scheduler show --scheduler hostfactory
```

### Validate Scheduler Configuration

Validate current scheduler configuration:

```bash
ohfp scheduler validate
```

Validate specific scheduler:

```bash
ohfp scheduler validate --scheduler default
```

## Global Scheduler Override

You can override the scheduler strategy for any command using the global `--scheduler` flag:

```bash
# Use default scheduler for templates command
ohfp --scheduler default templates list

# Use hostfactory scheduler for requests
ohfp --scheduler hostfactory requests create --template-id test --count 5

# Override scheduler for machine operations
ohfp --scheduler hf machines list
```

### Supported Schedulers

- `default` - Default scheduler using native domain fields
- `hostfactory` - Symphony HostFactory scheduler with field mapping
- `hf` - Alias for hostfactory scheduler

## Examples

### Basic Usage

```bash
# List available schedulers
$ ohfp scheduler list
{
  "strategies": [
    {
      "name": "default",
      "active": false,
      "registered": true
    },
    {
      "name": "hostfactory", 
      "active": true,
      "registered": true
    },
    {
      "name": "hf",
      "active": false,
      "registered": true
    }
  ],
  "current_strategy": "hostfactory",
  "total_count": 3
}

# Show current configuration
$ ohfp scheduler show
{
  "scheduler_name": "hostfactory",
  "configuration": {
    "type": "hostfactory",
    "config_root": "config"
  },
  "active": true,
  "valid": true,
  "found": true
}

# Validate configuration
$ ohfp scheduler validate
{
  "is_valid": true,
  "validation_errors": [],
  "warnings": []
}
```

### Detailed Information

```bash
# List with detailed information
$ ohfp scheduler list --long
{
  "strategies": [
    {
      "name": "default",
      "active": false,
      "registered": true,
      "description": "Default scheduler using native domain fields without conversion",
      "capabilities": ["native_domain_format", "direct_serialization", "minimal_conversion"]
    },
    {
      "name": "hostfactory",
      "active": true,
      "registered": true,
      "description": "Symphony HostFactory scheduler with field mapping and conversion",
      "capabilities": ["field_mapping", "format_conversion", "legacy_compatibility"]
    }
  ],
  "current_strategy": "hostfactory",
  "total_count": 3
}
```

### Global Override Usage

```bash
# Compare template output with different schedulers
$ ohfp --scheduler default templates list --format table
$ ohfp --scheduler hostfactory templates list --format table

# Use specific scheduler for machine requests
$ ohfp --scheduler default requests create --template-id aws-ec2-basic --count 3
```

## Error Handling

The scheduler commands provide detailed error messages for common issues:

### Invalid Scheduler

```bash
$ ohfp scheduler validate --scheduler unknown
{
  "is_valid": false,
  "validation_errors": [
    "Scheduler 'unknown' is not registered. Available: default, hostfactory, hf"
  ],
  "warnings": []
}
```

### Configuration Issues

```bash
$ ohfp scheduler validate
{
  "is_valid": false,
  "validation_errors": [
    "Scheduler strategy creation failed: Configuration missing"
  ],
  "warnings": [
    "No scheduler configuration section found in config"
  ]
}
```

## Integration with Other Commands

The scheduler system integrates seamlessly with all other CLI commands:

- Template operations use the scheduler for field mapping and response formatting
- Request operations use the scheduler for data parsing and validation
- Machine operations use the scheduler for status reporting and data conversion

The global `--scheduler` flag allows you to test different schedulers without changing configuration files, making it ideal for development and troubleshooting.

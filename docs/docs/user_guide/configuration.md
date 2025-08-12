# Configuration Guide

The Open Host Factory Plugin uses a centralized, type-safe configuration system that supports multiple sources and validation.

## Configuration Architecture

### Configuration Sources
Configuration is loaded from multiple sources in order of precedence:

1. **Environment Variables** (highest precedence)
2. **Configuration File** (if provided)
3. **Legacy Configuration** (backward compatibility)
4. **Default Values** (lowest precedence)

### Type Safety
All configuration uses dataclasses with comprehensive validation:

- **Compile-time validation**: Type checking with mypy
- **Runtime validation**: Pydantic models with business rules
- **Error reporting**: Detailed validation error messages

## Configuration Structure

### Complete Configuration Example

```json
{
  "provider": {
    "type": "aws",
    "region": "us-east-1",
    "profile": "default",
    "max_retries": 3,
    "timeout": 30,
    "access_key_id": null,
    "secret_access_key": null,
    "session_token": null
  },
  "logging": {
    "level": "INFO",
    "file_path": "logs/app.log",
    "console_enabled": true,
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "max_size": 10485760,
    "backup_count": 5
  },
  "database": {
    "type": "sqlite",
    "host": "",
    "port": 0,
    "name": "database.db",
    "username": null,
    "password": null
  },
  "template": {
    "default_image_id": "ami-12345678",
    "default_instance_type": "t2.micro",
    "subnet_ids": ["subnet-12345678"],
    "security_group_ids": ["sg-12345678"],
    "key_name": null,
    "user_data": null
  },
  "repository": {
    "type": "json",
    "json": {
      "storage_type": "single_file",
      "base_path": "data",
      "filenames": {
        "single_file": "request_database.json"
      }
    },
    "sql": {
      "connection_string": "sqlite:///database.db",
      "table_prefix": "hf_"
    },
    "dynamodb": {
      "table_name": "host_factory",
      "region": "us-east-1"
    }
  }
}
```

## Configuration Sections

### Provider Configuration

Controls cloud provider settings and authentication.

```json
{
  "provider": {
    "type": "aws",
    "region": "us-east-1",
    "profile": "default",
    "max_retries": 3,
    "timeout": 30,
    "access_key_id": null,
    "secret_access_key": null,
    "session_token": null
  }
}
```

**Fields:**
- `type`: Cloud provider type (`aws`, future: `provider1`, `provider2`)
- `region`: Cloud region for API calls
- `profile`: Credential profile to use
- `max_retries`: Number of API call retries (0-10)
- `timeout`: API call timeout in seconds (1-300)
- `access_key_id`: Direct access key (optional)
- `secret_access_key`: Direct secret key (optional)
- `session_token`: Session token for temporary credentials (optional)

### Logging Configuration

Comprehensive logging system with multiple outputs.

```json
{
  "logging": {
    "level": "INFO",
    "file_path": "logs/app.log",
    "console_enabled": true,
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "max_size": 10485760,
    "backup_count": 5
  }
}
```

**Fields:**
- `level`: Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)
- `file_path`: Path to log file (supports rotation)
- `console_enabled`: Enable console logging (boolean)
- `format`: Log message format string
- `max_size`: Maximum log file size in bytes
- `backup_count`: Number of backup log files to keep

### Database Configuration

Database settings for different storage backends.

```json
{
  "database": {
    "type": "sqlite",
    "host": "localhost",
    "port": 5432,
    "name": "hostfactory",
    "username": "user",
    "password": "password"
  }
}
```

**Supported Types:**
- `sqlite`: File-based SQLite database
- `postgresql`: PostgreSQL database
- `mysql`: MySQL database

**Fields:**
- `type`: Database type
- `host`: Database host (for networked databases)
- `port`: Database port
- `name`: Database name or file path
- `username`: Database username (optional)
- `password`: Database password (optional)

### Template Configuration

Default template settings and overrides.

```json
{
  "template": {
    "default_image_id": "ami-12345678",
    "default_instance_type": "t2.micro",
    "subnet_ids": ["subnet-12345678"],
    "security_group_ids": ["sg-12345678"],
    "key_name": "my-key-pair",
    "user_data": "#!/bin/bash\necho 'Hello World'"
  }
}
```

**Fields:**
- `default_image_id`: Default VM image identifier
- `default_instance_type`: Default instance type
- `subnet_ids`: List of subnet identifiers
- `security_group_ids`: List of security group identifiers
- `key_name`: SSH key pair name (optional)
- `user_data`: Instance initialization script (optional)

### Repository Configuration

Data persistence strategy configuration.

#### JSON Storage
```json
{
  "repository": {
    "type": "json",
    "json": {
      "storage_type": "single_file",
      "base_path": "data",
      "filenames": {
        "single_file": "database.json"
      }
    }
  }
}
```

#### SQL Storage
```json
{
  "repository": {
    "type": "sql",
    "sql": {
      "connection_string": "postgresql://user:pass@localhost:5432/hostfactory",
      "table_prefix": "hf_"
    }
  }
}
```

#### DynamoDB Storage
```json
{
  "repository": {
    "type": "dynamodb",
    "dynamodb": {
      "table_name": "host_factory_prod",
      "region": "us-east-1"
    }
  }
}
```

## Environment Variable Overrides

Any configuration value can be overridden using environment variables with the format:
`HF_<SECTION>_<FIELD>`

### Examples

```bash
# Override provider region
export HF_PROVIDER_REGION=us-west-2

# Override logging level
export HF_LOGGING_LEVEL=DEBUG

# Override database type
export HF_DATABASE_TYPE=postgresql

# Override template defaults
export HF_TEMPLATE_DEFAULT_INSTANCE_TYPE=t3.medium
```

### Nested Configuration

For nested configuration, use underscores:

```bash
# Override JSON storage path
export HF_REPOSITORY_JSON_BASE_PATH=/var/lib/hostfactory/data

# Override SQL connection string
export HF_REPOSITORY_SQL_CONNECTION_STRING=postgresql://localhost/hf
```

## Configuration Validation

### Automatic Validation

The system performs comprehensive validation:

```python
# Configuration is validated on load
config = ConfigurationManager.load_configuration("config.json")

# Validation errors are detailed and actionable
try:
    config.validate()
except ValidationError as e:
    for error in e.errors():
        print(f"Field: {error['loc']}")
        print(f"Error: {error['msg']}")
        print(f"Value: {error['input']}")
```

### Business Rule Validation

Beyond type checking, business rules are enforced:

- **Provider region**: Must be valid for the provider
- **Retry counts**: Must be reasonable (0-10)
- **Timeouts**: Must be positive and reasonable
- **File paths**: Must be accessible and writable
- **Database connections**: Connection strings must be valid

## Configuration Examples

### Development Configuration

```json
{
  "provider": {
    "type": "aws",
    "region": "us-east-1",
    "profile": "development"
  },
  "logging": {
    "level": "DEBUG",
    "console_enabled": true,
    "file_path": "logs/dev.log"
  },
  "database": {
    "type": "sqlite",
    "name": "dev_database.db"
  },
  "repository": {
    "type": "json",
    "json": {
      "storage_type": "single_file",
      "base_path": "data/dev"
    }
  }
}
```

### Production Configuration

```json
{
  "provider": {
    "type": "aws",
    "region": "us-east-1",
    "max_retries": 5,
    "timeout": 60
  },
  "logging": {
    "level": "INFO",
    "console_enabled": false,
    "file_path": "/var/log/hostfactory/app.log",
    "max_size": 104857600,
    "backup_count": 10
  },
  "database": {
    "type": "postgresql",
    "host": "db.example.com",
    "port": 5432,
    "name": "hostfactory",
    "username": "hf_user"
  },
  "repository": {
    "type": "sql",
    "sql": {
      "connection_string": "postgresql://hf_user:${DB_PASSWORD}@db.example.com:5432/hostfactory",
      "table_prefix": "hf_prod_"
    }
  }
}
```

### High-Availability Configuration

```json
{
  "provider": {
    "type": "aws",
    "region": "us-east-1",
    "max_retries": 10,
    "timeout": 120
  },
  "logging": {
    "level": "INFO",
    "file_path": "/var/log/hostfactory/app.log",
    "max_size": 209715200,
    "backup_count": 20
  },
  "repository": {
    "type": "dynamodb",
    "dynamodb": {
      "table_name": "hostfactory_ha",
      "region": "us-east-1"
    }
  }
}
```

## Legacy Configuration Support

The system maintains backward compatibility with existing configuration formats:

### Legacy AWS Provider Config
```json
{
  "region": "us-east-1",
  "profile": "default",
  "DEBUG": false,
  "LOG_LEVEL": "INFO",
  "LOG_FILE": "logs/awsprov.log"
}
```

### Automatic Migration
Legacy configurations are automatically converted:
- `DEBUG` -> `logging.level` (DEBUG if true, INFO if false)
- `LOG_FILE` -> `logging.file_path`
- `providerApi` -> `provider_api` (in templates)

## Configuration Management

### Loading Configuration

```python
from src.config.manager import get_config_manager

# Load from specific file
config = get_config_manager("/path/to/config.json")

# Load from default locations
config = get_config_manager()

# Access configuration sections
aws_config = config.get_provider_config()
logging_config = config.get_logging_config()
```

### Configuration Hot Reload

```python
# Watch for configuration changes
config_manager.watch_for_changes(callback=reload_handler)

# Reload configuration
config_manager.reload()
```

## Troubleshooting

### Common Configuration Issues

#### Invalid JSON Format
```bash
# Validate JSON syntax
python -m json.tool config.json
```

#### Missing Required Fields
```bash
# Use configuration validator
hostfactory validate-config --config config.json
```

#### Environment Variable Issues
```bash
# Check environment variables
env | grep HF_

# Test environment override
HF_LOGGING_LEVEL=DEBUG hostfactory test-config
```

### Configuration Debugging

Enable debug logging to see configuration loading:

```bash
export HF_LOGGING_LEVEL=DEBUG
hostfactory --config config.json
```

This will show:
- Configuration sources checked
- Values loaded from each source
- Environment variable overrides applied
- Validation results

## Best Practices

### Security
- **Never commit credentials** to version control
- **Use environment variables** for sensitive values
- **Restrict file permissions** on configuration files
- **Use IAM roles** when possible instead of access keys

### Organization
- **Separate environments**: Different configs for dev/staging/prod
- **Version control**: Track configuration changes
- **Documentation**: Document custom configuration values
- **Validation**: Always validate configuration before deployment

### Performance
- **Appropriate timeouts**: Balance reliability and performance
- **Retry strategies**: Configure retries based on workload
- **Logging levels**: Use appropriate levels for environment
- **Storage strategy**: Choose optimal persistence for scale

## Next Steps

- **[Templates](templates.md)**: Configure VM templates
- **[Deployment](deployment.md)**: Production deployment configuration
- **[Monitoring](monitoring.md)**: Configure monitoring and alerting
- **[API Reference](api_reference.md)**: Configuration API reference

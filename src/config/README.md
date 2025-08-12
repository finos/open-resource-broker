# Configuration Management - Centralized Configuration System

The configuration package provides a comprehensive, type-safe configuration management system with support for multiple sources, validation, and legacy compatibility.

## Architecture Overview

### Centralized Configuration Approach
The configuration system serves as the single source of truth for all application settings:

- **Type Safety**: Configuration defined using dataclasses with validation
- **Multiple Sources**: Environment variables, files, legacy configs, defaults
- **Validation**: Comprehensive configuration validation and error reporting
- **Legacy Support**: Backward compatibility with existing configuration formats
- **Centralized Access**: Single configuration manager for all components

### Key Components

#### `manager.py` - Configuration Manager
Central configuration management with centralized access patterns.

**Key Features:**
- Single source of truth for all configuration
- Type-safe configuration access
- Environment variable override support
- Configuration validation and error handling
- Legacy configuration integration

#### `loader.py` - Configuration Loader
Handles loading configuration from multiple sources with precedence rules.

**Key Features:**
- Multi-source configuration loading
- Source precedence management
- Format detection and parsing
- Error handling and reporting

#### `validator.py` - Configuration Validator
Comprehensive validation system for all configuration sections.

**Key Features:**
- Type-safe validation using Pydantic models
- Business rule validation
- Configuration completeness checking
- Detailed error reporting

## Configuration Structure

### Core Configuration Sections

#### Cloud Provider Configuration
```json
{
  "aws": {
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
- `region`: Cloud region for API calls
- `profile`: Credential profile to use
- `max_retries`: Number of API call retries
- `timeout`: API call timeout in seconds
- `access_key_id`: Direct access key (optional)
- `secret_access_key`: Direct secret key (optional)
- `session_token`: Session token for temporary credentials (optional)

#### Logging Configuration
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
- `level`: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `file_path`: Path to log file
- `console_enabled`: Enable console logging
- `format`: Log message format string
- `max_size`: Maximum log file size in bytes
- `backup_count`: Number of backup log files to keep

#### Database Configuration
```json
{
  "database": {
    "type": "sqlite",
    "host": "",
    "port": 0,
    "name": "database.db",
    "username": null,
    "password": null
  }
}
```

**Fields:**
- `type`: Database type (sqlite, postgresql, mysql)
- `host`: Database host (for networked databases)
- `port`: Database port
- `name`: Database name or file path
- `username`: Database username (optional)
- `password`: Database password (optional)

#### Template Configuration
```json
{
  "template": {
    "default_image_id": "ami-12345678",
    "default_instance_type": "t2.micro",
    "subnet_ids": ["subnet-12345678"],
    "security_group_ids": ["sg-12345678"],
    "key_name": null,
    "user_data": null
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

#### Repository Configuration
```json
{
  "REPOSITORY_CONFIG": {
    "type": "json",
    "json": {
      "storage_type": "single_file",
      "base_path": "data",
      "filenames": {
        "single_file": "request_database.json",
        "multi_file": {
          "requests": "requests.json",
          "machines": "machines.json",
          "templates": "templates.json"
        }
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

**Repository Types:**
- `json`: JSON file-based storage
- `sql`: SQL database storage
- `dynamodb`: DynamoDB storage

## Configuration Loading

### Source Precedence
Configuration is loaded from multiple sources in order of precedence:

1. **Environment Variables** (highest precedence)
2. **Configuration File** (if provided)
3. **Legacy Configuration** (if available)
4. **Default Values** (lowest precedence)

### Environment Variable Override
Any configuration value can be overridden using environment variables:

```bash
# Override AWS region
export HF_AWS_REGION=us-west-2

# Override logging level
export HF_LOGGING_LEVEL=DEBUG

# Override database type
export HF_DATABASE_TYPE=postgresql
```

**Environment Variable Format:**
- Prefix: `HF_`
- Section: `AWS_`, `LOGGING_`, `DATABASE_`, etc.
- Field: `REGION`, `LEVEL`, `TYPE`, etc.
- Full example: `HF_AWS_REGION`, `HF_LOGGING_LEVEL`

### Configuration File Loading
```python
from src.config.manager import get_config_manager

# Load from specific file
config_manager = get_config_manager("/path/to/config.json")

# Load from default locations
config_manager = get_config_manager()  # Searches standard locations
```

## Configuration Manager Usage

### Basic Usage
```python
from src.config.manager import get_config_manager

# Get configuration manager
config = get_config_manager()

# Access configuration sections
aws_config = config.get_aws_config()
logging_config = config.get_logging_config()
database_config = config.get_database_config()

# Access specific values
region = config.get_aws_config().region
log_level = config.get_logging_config().level
```

### Type-Safe Access
```python
# Configuration is type-safe with dataclasses
aws_config = config.get_aws_config()
assert isinstance(aws_config.region, str)
assert isinstance(aws_config.max_retries, int)
assert isinstance(aws_config.timeout, int)

# Optional fields are properly typed
if aws_config.access_key_id is not None:
    # Type checker knows this is a string
    print(f"Using access key: {aws_config.access_key_id[:8]}...")
```

### Configuration Validation
```python
from src.config.validator import validate_configuration

try:
    # Validate complete configuration
    config_data = load_config_from_file("config.json")
    validated_config = validate_configuration(config_data)

except ValidationError as e:
    print(f"Configuration validation failed: {e}")
    for error in e.errors():
        print(f"  - {error['loc']}: {error['msg']}")
```

## Legacy Configuration Support

### Legacy Format Compatibility
The system maintains backward compatibility with existing configuration formats:

#### Legacy AWS Provider Config (`awsprov_config.json`)
```json
{
  "region": "us-east-1",
  "profile": "default",
  "DEBUG": false,
  "LOG_LEVEL": "INFO",
  "LOG_FILE": "logs/awsprov.log"
}
```

#### Legacy Template Config (`awsprov_templates.json`)
```json
{
  "templates": [
    {
      "templateId": "template-1",
      "name": "Standard Template",
      "providerApi": "ec2_fleet",
      "imageId": "ami-12345678",
      "instanceType": "t2.micro"
    }
  ]
}
```

### Legacy Migration
```python
from src.config.loader import ConfigurationLoader

loader = ConfigurationLoader()

# Load and migrate legacy configuration
config_data = loader.load_configuration(
    config_path=None,  # Will search for legacy files
    legacy_support=True
)

# Legacy fields are automatically converted to new format
# providerApi -> provider_api
# DEBUG -> logging.level
# LOG_FILE -> logging.file_path
```

## Configuration Validation

### Validation Rules
The validator enforces comprehensive business rules:

#### AWS Configuration Validation
```python
def validate_aws_config(aws_config: AWSConfig) -> None:
    """Validate AWS configuration."""
    # Region validation
    if not aws_config.region:
        raise ValueError("AWS region is required")

    # Retry validation
    if aws_config.max_retries < 0:
        raise ValueError("Max retries cannot be negative")

    # Timeout validation
    if aws_config.timeout <= 0:
        raise ValueError("Timeout must be positive")

    # Credential validation
    if aws_config.access_key_id and not aws_config.secret_access_key:
        raise ValueError("Secret access key required when access key provided")
```

#### Repository Configuration Validation
```python
def validate_repository_config(repo_config: RepositoryConfig) -> None:
    """Validate repository configuration."""
    if repo_config.type == "json":
        if not repo_config.json.base_path:
            raise ValueError("JSON base path is required")

        if repo_config.json.storage_type == "single_file":
            if not repo_config.json.filenames.single_file:
                raise ValueError("Single file name is required")

    elif repo_config.type == "sql":
        if not repo_config.sql.connection_string:
            raise ValueError("SQL connection string is required")

    elif repo_config.type == "dynamodb":
        if not repo_config.dynamodb.table_name:
            raise ValueError("DynamoDB table name is required")
```

## Configuration Examples

### Development Configuration
```json
{
  "aws": {
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
  "REPOSITORY_CONFIG": {
    "type": "json",
    "json": {
      "storage_type": "single_file",
      "base_path": "data/dev",
      "filenames": {
        "single_file": "dev_requests.json"
      }
    }
  }
}
```

### Production Configuration
```json
{
  "aws": {
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
    "name": "hostfactory"
  },
  "REPOSITORY_CONFIG": {
    "type": "sql",
    "sql": {
      "connection_string": "postgresql://user:pass@db.example.com:5432/hostfactory",
      "table_prefix": "hf_prod_"
    }
  }
}
```

## Error Handling

### Configuration Errors
```python
class ConfigurationError(Exception):
    """Base configuration error."""
    pass

class ConfigurationNotFoundError(ConfigurationError):
    """Configuration file not found."""
    pass

class ConfigurationValidationError(ConfigurationError):
    """Configuration validation failed."""
    pass

class LegacyConfigurationError(ConfigurationError):
    """Legacy configuration processing failed."""
    pass
```

### Error Recovery
```python
def load_configuration_with_fallback():
    """Load configuration with fallback to defaults."""
    try:
        return get_config_manager("/etc/hostfactory/config.json")
    except ConfigurationNotFoundError:
        logger.warning("Primary config not found, trying fallback")
        try:
            return get_config_manager("./config.json")
        except ConfigurationNotFoundError:
            logger.warning("Fallback config not found, using defaults")
            return get_config_manager()  # Uses defaults
```

## Testing Configuration

### Configuration Testing
```python
def test_configuration_loading():
    """Test configuration loading and validation."""
    # Test valid configuration
    config_data = {
        "aws": {"region": "us-east-1"},
        "logging": {"level": "INFO"}
    }

    config = validate_configuration(config_data)
    assert config.aws.region == "us-east-1"
    assert config.logging.level == "INFO"

def test_environment_override():
    """Test environment variable override."""
    import os

    # Set environment variable
    os.environ["HF_AWS_REGION"] = "us-west-2"

    config = get_config_manager()
    assert config.get_aws_config().region == "us-west-2"

    # Cleanup
    del os.environ["HF_AWS_REGION"]
```

## Future Extensions

### Configuration Hot Reload
```python
class ConfigurationWatcher:
    """Watch configuration files for changes."""

    def __init__(self, config_path: str, callback: Callable):
        self._config_path = config_path
        self._callback = callback

    def start_watching(self):
        """Start watching for configuration changes."""
        # Implementation for file system watching
        pass
```

### Configuration Encryption
```python
class EncryptedConfigurationLoader:
    """Load encrypted configuration files."""

    def load_encrypted_config(self, config_path: str, key: str) -> Dict[str, Any]:
        """Load and decrypt configuration."""
        # Implementation for encrypted configuration
        pass
```

---

This configuration system provides a robust, flexible, and type-safe foundation for managing all application settings while maintaining backward compatibility and supporting multiple deployment scenarios.

This section configures default values for VM templates:

```json
"template": {
  "default_image_id": "ami-12345678",
  "default_instance_type": "t2.micro",
  "subnet_ids": ["subnet-12345678"],
  "security_group_ids": ["sg-12345678"],
  "default_key_name": "",
  "default_max_number": 10,
  "default_attributes": {
    "type": ["String", "X86_64"],
    "ncpus": ["Numeric", "1"],
    "nram": ["Numeric", "1024"],
    "ncores": ["Numeric", "1"]
  },
  "default_instance_tags": {
    "company": "abc",
    "project": "awscloud",
    "team": "xyz"
  },
  "ssm_parameter_prefix": "/hostfactory/templates/",
  "templates_file_path": "config/templates.json"
}
```

- `default_image_id`: Default AMI ID for templates
- `default_instance_type`: Default EC2 instance type
- `subnet_ids`: Default subnet IDs for templates
- `security_group_ids`: Default security group IDs for templates
- `default_key_name`: Default SSH key name
- `default_max_number`: Default maximum number of instances per template
- `default_attributes`: Default attributes for templates (used by Host Factory)
- `default_instance_tags`: Default tags to apply to instances
- `ssm_parameter_prefix`: Prefix for SSM parameters containing template overrides
- `templates_file_path`: Path to the templates configuration file

### Templates File

Templates are defined in a separate file specified by `templates_file_path` (default: `config/templates.json`). This separation allows for better organization and avoids mixing configuration concerns.

Example templates file:

```json
{
  "templates": [
    {
      "templateId": "OnDemand-Minimal-Template-VM",
      "maxNumber": 10,
      "aws_handler": "RunInstances",
      "image_id": "ami-12345678",
      "vm_type": "t2.micro",
      "subnet_id": "subnet-12345678",
      "security_group_ids": ["sg-12345678"],
      "key_name": "",
      "attributes": {
        "type": ["String", "X86_64"],
        "ncpus": ["Numeric", "1"],
        "nram": ["Numeric", "1024"],
        "ncores": ["Numeric", "1"]
      }
    },
    {
      "templateId": "Spot-Template-VM",
      "maxNumber": 10,
      "aws_handler": "SpotFleet",
      "image_id": "ami-12345678",
      "vm_type": "m3.medium",
      "subnet_id": "subnet-12345678",
      "security_group_ids": ["sg-12345678"],
      "key_name": "",
      "attributes": {
        "type": ["String", "X86_64"],
        "ncpus": ["Numeric", "1"],
        "nram": ["Numeric", "1024"],
        "ncores": ["Numeric", "1"]
      },
      "instance_tags": {
        "company": "abc",
        "project": "awscloud",
        "team": "xyz"
      }
    }
  ]
}
```

### SSM Parameter Integration

Templates can be overridden or extended using AWS SSM Parameters. The application will check for parameters with the prefix specified in `ssm_parameter_prefix` (default: `/hostfactory/templates/`).

For example, if you have a template with ID `OnDemand-Minimal-Template-VM`, you can override its properties by creating SSM parameters:

- `/hostfactory/templates/OnDemand-Minimal-Template-VM/image_id` - Override the AMI ID
- `/hostfactory/templates/OnDemand-Minimal-Template-VM/vm_type` - Override the instance type
- `/hostfactory/templates/OnDemand-Minimal-Template-VM/maxNumber` - Override the maximum number of instances

This allows for dynamic configuration without modifying the templates file.

### Events Configuration (`events`)

This section configures the event system:

```json
"events": {
  "store_type": "memory",
  "publisher_type": "composite",
  "enable_logging": true
}
```

- `store_type`: Type of event store (memory, file, sqlite)
- `publisher_type`: Type of event publisher (memory, logging, composite)
- `enable_logging`: Whether to log events

### Storage Configuration (`storage`)

This section configures how the application stores its state using the strategy pattern:

```json
"storage": {
  "strategy": "json",
  "json_strategy": {
    "storage_type": "single_file",
    "base_path": "data",
    "filenames": {
      "single_file": "request_database.json",
      "split_files": {
        "templates": "templates.json",
        "requests": "requests.json",
        "machines": "machines.json"
      }
    }
  },
  "sql_strategy": {
    "type": "sqlite",
    "host": "",
    "port": 0,
    "name": "database.db",
    "pool_size": 5,
    "max_overflow": 10,
    "timeout": 30
  },
  "dynamodb_strategy": {
    "region": "us-east-1",
    "profile": "default",
    "table_prefix": "hostfactory"
  }
}
```

- `strategy`: Storage strategy to use (json, sql, dynamodb)
- `json_strategy`: JSON storage strategy configuration
  - `storage_type`: Storage type (single_file, split_files)
  - `base_path`: Base path for JSON files
  - `filenames`: Filenames for JSON files
- `sql_strategy`: SQL storage strategy configuration
  - `type`: Database type (sqlite, postgresql, mysql)
  - `host`: Database host
  - `port`: Database port
  - `name`: Database name
  - `pool_size`: Connection pool size
  - `max_overflow`: Maximum number of connections to overflow
  - `timeout`: Connection timeout in seconds
- `dynamodb_strategy`: DynamoDB storage strategy configuration
  - `region`: AWS region for DynamoDB
  - `profile`: AWS profile for DynamoDB
  - `table_prefix`: Prefix for DynamoDB tables

## Configuration Loading Order

The configuration is loaded from multiple sources in the following order (later sources override earlier ones):

1. Default configuration from `config/default_config.json`
2. Legacy configuration from `HF_PROVIDER_CONFDIR/awsprov_config.json` and `HF_PROVIDER_CONFDIR/awsprov_templates.json`
3. Configuration file specified at runtime
4. Environment variables

## Environment Variables

You can override configuration values using environment variables:

- Standard environment variables (e.g., `AWS_REGION`, `LOG_LEVEL`)
- Legacy environment variables with `HF_` prefix (e.g., `HF_PROVIDER_CONFDIR`)

## Storage Strategy Pattern

The application uses the Strategy Pattern for storage configuration, which provides several benefits:

1. **Better Code Organization**: The strategy pattern provides a clearer separation of concerns between different storage mechanisms.

2. **Improved Extensibility**: Adding new storage strategies is now easier and more structured.

3. **Type Safety**: Each strategy has its own typed configuration class, improving type safety and IDE support.

4. **Cleaner Configuration**: The configuration structure is more intuitive and follows a consistent pattern.

### Using Storage Configuration in Code

```python
from src.config.settings import StorageConfig
from src.config.manager import get_config_manager

# Get storage configuration
config_manager = get_config_manager()
storage_config = config_manager.get_typed(StorageConfig)

# Use storage configuration
strategy = storage_config.strategy
if strategy == 'json':
    # Use JSON strategy
    json_config = storage_config.json_strategy
    # ...
elif strategy == 'sql':
    # Use SQL strategy
    sql_config = storage_config.sql_strategy
    # ...
elif strategy == 'dynamodb':
    # Use DynamoDB strategy
    dynamodb_config = storage_config.dynamodb_strategy
    # ...

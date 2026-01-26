# Configuration Documentation

This document explains the purpose and structure of the configuration used by the AWS Host Factory Plugin.

## Configuration Structure

The configuration is structured into several sections, each serving a specific purpose:

### AWS Configuration (`aws`)

This section configures AWS SDK behavior:

```json
"aws": {
  "region": "us-east-1",
  "profile": "default",
  "aws_max_retries": 3,
  "aws_connect_timeout": 10,
  "aws_read_timeout": 30
}
```

- `region`: AWS region to use for API calls
- `profile`: AWS profile to use for credentials
- `aws_max_retries`: Number of retries for AWS API calls
- `aws_connect_timeout`: Connection timeout in seconds for AWS API calls
- `aws_read_timeout`: Timeout in seconds for AWS API calls

### Logging Configuration (`logging`)

This section configures the logging behavior:

```json
"logging": {
  "level": "INFO",
  "file_path": "logs/app.log",
  "console_enabled": true,
  "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
  "max_size": 10485760,
  "backup_count": 5
}
```

- `level`: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `file_path`: Path to log file
- `console_enabled`: Whether to log to console
- `format`: Log message format
- `max_size`: Maximum log file size in bytes
- `backup_count`: Number of backup log files to keep

### Template Configuration (`template`)

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
      "provider_api": "RunInstances",
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
      "provider_api": "SpotFleet",
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

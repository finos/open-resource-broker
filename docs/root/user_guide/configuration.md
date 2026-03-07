# Configuration Guide

The Open Resource Broker uses a centralized, type-safe configuration system that supports multiple sources and validation.

## Configuration Architecture

### Configuration Sources and Precedence

The Open Resource Broker uses a sophisticated configuration system that loads settings from multiple sources with clear precedence rules. This enables flexible deployment scenarios while maintaining security and operational best practices.

#### Configuration Source Hierarchy

Configuration is loaded from multiple sources in strict order of precedence:

1. **Environment Variables** (highest precedence)
   - `ORB_*` variables for core application settings
   - `ORB_AWS_*` variables for AWS provider settings
   - `HF_*` variables for HostFactory scheduler compatibility
   - Automatic type conversion and validation

2. **Configuration File** (medium precedence)
   - JSON configuration file specified via `--config` flag
   - Default locations: `config/config.json`, `~/.orb/config.json`
   - Comprehensive validation and error reporting

3. **Provider Template Defaults** (low precedence)
   - Infrastructure defaults discovered during `orb init --interactive`
   - Provider-specific defaults from template_defaults section
   - Automatically populated subnet IDs, security groups, etc.

4. **System Defaults** (lowest precedence)
   - Built-in default values for all configuration fields
   - Ensures system operates with minimal configuration
   - Development-friendly defaults

#### Configuration Loading Process

The configuration system follows this loading sequence:

```
1. Load system defaults
2. Apply configuration file values (if present)
3. Apply provider template defaults (if configured)
4. Apply environment variable overrides
5. Validate complete configuration
6. Report any validation errors
```

#### Environment Variable Precedence Examples

Environment variables always override configuration file values:

```bash
# Configuration file contains:
# {"logging": {"level": "INFO"}}

# Environment variable overrides:
export ORB_LOG_LEVEL=DEBUG

# Result: DEBUG level is used (environment variable wins)
```

```bash
# Configuration file contains:
# {"provider": {"config": {"region": "us-east-1"}}}

# Environment variable overrides:
export ORB_AWS_REGION=us-west-2

# Result: us-west-2 region is used (environment variable wins)
```

### Type Safety and Validation

All configuration uses Pydantic BaseSettings with comprehensive validation:

#### Compile-Time Validation
- **Type checking**: Full mypy support for static analysis
- **IDE support**: Autocomplete and type hints in development
- **Refactoring safety**: Type-safe configuration changes

#### Runtime Validation
- **Pydantic models**: Automatic type conversion and validation
- **Business rules**: Custom validators for complex constraints
- **Error reporting**: Detailed validation error messages with field paths

#### Validation Examples

```python
# Valid configuration
ORB_LOG_LEVEL=DEBUG                    # ✅ Valid log level
ORB_AWS_REGION=us-east-1               # ✅ Valid AWS region
ORB_REQUEST_TIMEOUT=300                # ✅ Valid timeout (1-3600 seconds)
ORB_AWS_SUBNET_IDS='["subnet-123"]'    # ✅ Valid JSON array

# Invalid configuration (validation errors)
ORB_LOG_LEVEL=INVALID                  # ❌ Invalid log level
ORB_AWS_REGION=invalid-region          # ❌ Invalid AWS region format
ORB_REQUEST_TIMEOUT=-1                 # ❌ Negative timeout not allowed
ORB_AWS_SUBNET_IDS='invalid-json'      # ❌ Invalid JSON format
```

### Configuration Scenarios

#### Development Environment

**Characteristics:**
- Minimal configuration required
- Debug logging enabled
- Local file storage
- Relaxed timeouts and retries

**Configuration approach:**
```bash
# Minimal environment variables
export ORB_ENVIRONMENT=development
export ORB_LOG_LEVEL=DEBUG
export ORB_DEBUG=true

# AWS development account
export ORB_AWS_REGION=us-east-1
export ORB_AWS_PROFILE=development

# Start with defaults
orb system serve
```

#### Staging Environment

**Characteristics:**
- Production-like configuration
- Moderate logging
- Shared infrastructure
- Realistic performance settings

**Configuration approach:**
```bash
# Staging environment setup
export ORB_ENVIRONMENT=staging
export ORB_LOG_LEVEL=INFO

# AWS staging account
export ORB_AWS_REGION=us-east-1
export ORB_AWS_PROFILE=staging
export ORB_AWS_SUBNET_IDS='["subnet-staging123", "subnet-staging456"]'
export ORB_AWS_SECURITY_GROUP_IDS='["sg-staging123"]'

# Production-like performance
export ORB_REQUEST_TIMEOUT=300
export ORB_AWS_MAX_RETRIES=3

# Use configuration file for complex settings
orb system serve --config config/staging.json
```

#### Production Environment

**Characteristics:**
- Comprehensive configuration
- Structured logging
- High availability settings
- Security-focused configuration

**Configuration approach:**
```bash
# Production environment (environment variables only for sensitive data)
export ORB_ENVIRONMENT=production
export ORB_AWS_ROLE_ARN=arn:aws:iam::123456789012:role/OrbitProductionRole

# All other configuration in secure configuration file
orb system serve --config /etc/orb/production.json
```

### Configuration File Structure

#### Minimal Configuration
```json
{
  "provider": {
    "type": "aws",
    "config": {
      "region": "us-east-1",
      "profile": "default"
    }
  }
}
```

#### Complete Configuration
```json
{
  "provider": {
    "type": "aws",
    "config": {
      "region": "us-east-1",
      "profile": "production",
      "max_retries": 5,
      "timeout": 120
    },
    "template_defaults": {
      "subnet_ids": ["subnet-12345678", "subnet-87654321"],
      "security_group_ids": ["sg-abcdef12"],
      "key_name": "production-key-pair",
      "instance_type": "t3.medium"
    }
  },
  "scheduler": {
    "type": "hostfactory",
    "config_dir": "/etc/hostfactory/config",
    "work_dir": "/var/lib/hostfactory/work",
    "log_dir": "/var/log/hostfactory"
  },
  "logging": {
    "level": "INFO",
    "file_path": "/var/log/orb/app.log",
    "console_enabled": false,
    "max_size": 104857600,
    "backup_count": 10
  },
  "storage": {
    "strategy": "json",
    "config": {
      "base_path": "/var/lib/orb/data",
      "backup_enabled": true,
      "backup_count": 5
    }
  }
}
```

### Configuration Best Practices

#### Security
- **Environment variables for secrets**: Never store credentials in configuration files
- **IAM roles preferred**: Use IAM roles instead of access keys when possible
- **File permissions**: Restrict configuration file permissions (600 or 640)
- **Version control**: Never commit sensitive configuration to version control

#### Organization
- **Environment separation**: Separate configuration files for dev/staging/prod
- **Configuration validation**: Always validate configuration before deployment
- **Documentation**: Document custom configuration values and their purposes
- **Change tracking**: Track configuration changes through version control

#### Performance
- **Appropriate timeouts**: Balance reliability and performance based on workload
- **Retry strategies**: Configure retries based on expected failure patterns
- **Resource limits**: Set appropriate limits for concurrent operations
- **Monitoring integration**: Configure logging and metrics for operational visibility

### Configuration Troubleshooting

#### Common Issues

**Configuration file not found:**
```bash
# Check default locations
ls -la config/config.json ~/.orb/config.json

# Specify explicit path
orb system serve --config /path/to/config.json
```

**Environment variable not taking effect:**
```bash
# Verify environment variable is set
env | grep ORB_

# Check configuration loading with debug
ORB_LOG_LEVEL=DEBUG orb system health
```

**Validation errors:**
```bash
# Validate configuration
orb system validate-config

# Test specific provider configuration
orb providers health aws_production_us-east-1
```

#### Debug Configuration Loading

Enable debug logging to see configuration loading process:

```bash
export ORB_LOG_LEVEL=DEBUG
orb system serve --config config.json
```

This will show:
- Configuration sources checked
- Values loaded from each source
- Environment variable overrides applied
- Validation results and any errors
- Final merged configuration

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

## Config Directory Resolution

`orb init` creates configuration files in a directory determined by your install type. The resolution order is:

1. **`ORB_CONFIG_DIR` environment variable** — always wins if set
2. **Virtual environment** — `{venv_parent}/config/` (standard venvs and symlink venvs like uv/mise)
3. **Development mode** — `{project_root}/config/` (detected by `pyproject.toml` in parent directories)
4. **User install** (`pip install --user`) — `~/.local/orb/config/`
5. **System install** — `{sys.prefix}/orb/config/`
6. **Fallback** — `{cwd}/config/`

Work, logs, and scripts directories are siblings of the config directory (e.g. `{venv_parent}/work/`, `{venv_parent}/logs/`). Override individually with `ORB_WORK_DIR`, `ORB_LOG_DIR`.

## AWS IAM Permissions

ORB requires the following IAM permissions for the AWS provider. Scope the `Resource` field to your account/region as needed.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeImages",
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceTypes",
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:CreateFleet",
        "ec2:DeleteFleet",
        "ec2:DescribeFleets",
        "ec2:RequestSpotFleet",
        "ec2:CancelSpotFleetRequests",
        "ec2:DescribeSpotFleetRequests",
        "ec2:DescribeSpotFleetInstances",
        "ec2:CreateTags",
        "autoscaling:CreateAutoScalingGroup",
        "autoscaling:UpdateAutoScalingGroup",
        "autoscaling:DeleteAutoScalingGroup",
        "autoscaling:DescribeAutoScalingGroups",
        "autoscaling:CreateLaunchConfiguration",
        "autoscaling:DeleteLaunchConfiguration",
        "ec2:CreateLaunchTemplate",
        "ec2:DeleteLaunchTemplate",
        "ec2:DescribeLaunchTemplates",
        "iam:PassRole"
      ],
      "Resource": "*"
    }
  ]
}
```

For SpotFleet, you also need the `AWSServiceRoleForEC2SpotFleet` service-linked role. Create it if it doesn't exist:

```bash
aws iam create-service-linked-role --aws-service-name spotfleet.amazonaws.com
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

## Environment Variable Reference

The Open Resource Broker provides comprehensive environment variable support using Pydantic BaseSettings for automatic type conversion, validation, and configuration management.

### Environment Variable Naming Convention

Environment variables follow a hierarchical naming pattern:
- **Core settings**: `ORB_<FIELD_NAME>`
- **AWS provider**: `ORB_AWS_<FIELD_NAME>`
- **Nested objects**: `ORB_<SECTION>__<FIELD_NAME>` (double underscore)

### Core Application Variables

#### Application Behavior
```bash
ORB_LOG_LEVEL=DEBUG                    # Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL
ORB_DEBUG=true                         # Enable debug mode (boolean)
ORB_ENVIRONMENT=production             # Environment identifier (string)
ORB_REQUEST_TIMEOUT=600                # Global request timeout in seconds (integer)
ORB_MAX_MACHINES_PER_REQUEST=200       # Maximum machines per single request (integer)
```

#### Directory Configuration
```bash
ORB_CONFIG_DIR=/opt/orb/config         # Configuration files directory
ORB_WORK_DIR=/opt/orb/work             # Working directory for temporary files
ORB_LOG_DIR=/opt/orb/logs              # Log files directory
```

### AWS Provider Variables

#### Authentication and Region
```bash
ORB_AWS_REGION=us-west-2               # AWS region (string)
ORB_AWS_PROFILE=production             # AWS credential profile name (string)
ORB_AWS_ROLE_ARN=arn:aws:iam::123456789012:role/OrbitRole  # IAM role ARN (string)
ORB_AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE                 # AWS access key ID (string)
ORB_AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY  # AWS secret access key (string)
ORB_AWS_SESSION_TOKEN=AQoEXAMPLEH4aoAH0gNCAPyJxz4ARKDHxyP5XpAa  # Session token (string)
ORB_AWS_ENDPOINT_URL=https://ec2.us-west-2.amazonaws.com   # Custom endpoint URL (string)
```

#### Infrastructure Defaults
```bash
# Infrastructure discovered during 'orb init --interactive'
ORB_AWS_SUBNET_IDS='["subnet-12345678", "subnet-87654321"]'           # JSON array of subnet IDs
ORB_AWS_SECURITY_GROUP_IDS='["sg-abcdef12", "sg-34567890"]'           # JSON array of security group IDs
ORB_AWS_KEY_NAME=my-production-key     # EC2 key pair name (string)
ORB_AWS_IMAGE_ID=ami-0abcdef1234567890  # Default AMI ID (string)
ORB_AWS_INSTANCE_TYPE=t3.medium        # Default instance type (string)
```

#### AWS Service Configuration
```bash
ORB_AWS_MAX_RETRIES=5                  # Maximum API retry attempts (integer, 0-10)
ORB_AWS_TIMEOUT=120                    # AWS API timeout in seconds (integer, 1-300)
ORB_AWS_USE_SSL=true                   # Use SSL for AWS API calls (boolean)
ORB_AWS_VERIFY_SSL=true                # Verify SSL certificates (boolean)
```

### Nested Configuration Variables

For complex configuration objects, use double underscores (`__`) to separate levels:

#### Circuit Breaker Configuration
```bash
ORB_CIRCUIT_BREAKER__ENABLED=true                    # Enable circuit breaker (boolean)
ORB_CIRCUIT_BREAKER__FAILURE_THRESHOLD=10            # Failures before opening circuit (integer)
ORB_CIRCUIT_BREAKER__RECOVERY_TIMEOUT=120            # Recovery timeout in seconds (integer)
ORB_CIRCUIT_BREAKER__HALF_OPEN_MAX_CALLS=3           # Max calls in half-open state (integer)
```

#### Retry Configuration
```bash
ORB_RETRY__MAX_ATTEMPTS=5              # Maximum retry attempts (integer)
ORB_RETRY__BACKOFF_MULTIPLIER=2.0      # Exponential backoff multiplier (float)
ORB_RETRY__MAX_DELAY=60                # Maximum delay between retries in seconds (integer)
ORB_RETRY__JITTER=true                 # Add random jitter to delays (boolean)
```

#### Rate Limiting Configuration
```bash
ORB_RATE_LIMIT__REQUESTS_PER_MINUTE=100              # Requests per minute limit (integer)
ORB_RATE_LIMIT__BURST_SIZE=20                        # Burst request allowance (integer)
ORB_RATE_LIMIT__ENABLED=true                         # Enable rate limiting (boolean)
```

### Scheduler-Specific Variables

Different scheduler strategies support specific environment variables for backward compatibility:

#### HostFactory Scheduler (Legacy Support)
```bash
HF_PROVIDER_WORKDIR=/var/lib/hostfactory/work        # HostFactory working directory
HF_PROVIDER_CONFDIR=/etc/hostfactory/config          # HostFactory configuration directory
HF_PROVIDER_LOGDIR=/var/log/hostfactory              # HostFactory log directory
HF_LOGLEVEL=DEBUG                                    # HostFactory-specific log level
HF_LOGGING_CONSOLE_ENABLED=false                     # Disable console output for JSON-only mode
HF_PROVIDER_ACTION_TIMEOUT=300                       # Action timeout for HostFactory operations
```

#### Default Scheduler
```bash
DEFAULT_PROVIDER_WORKDIR=/opt/orb/work               # Default scheduler working directory
DEFAULT_PROVIDER_CONFDIR=/opt/orb/config             # Default scheduler configuration directory
DEFAULT_PROVIDER_LOGDIR=/opt/orb/logs                # Default scheduler log directory
```

### Type Conversion and Validation

Environment variables are automatically converted to appropriate types:

#### Boolean Values
```bash
# All of these evaluate to True
ORB_DEBUG=true
ORB_DEBUG=True
ORB_DEBUG=TRUE
ORB_DEBUG=1
ORB_DEBUG=yes
ORB_DEBUG=on

# All of these evaluate to False
ORB_DEBUG=false
ORB_DEBUG=False
ORB_DEBUG=FALSE
ORB_DEBUG=0
ORB_DEBUG=no
ORB_DEBUG=off
```

#### JSON Arrays and Objects
```bash
# JSON arrays (automatically parsed)
ORB_AWS_SUBNET_IDS='["subnet-123", "subnet-456", "subnet-789"]'
ORB_AWS_SECURITY_GROUP_IDS='["sg-abc", "sg-def"]'

# JSON objects (for complex nested configuration)
ORB_LAUNCH_TEMPLATE='{"LaunchTemplateName": "my-template", "Version": "1"}'
```

#### Numeric Values
```bash
# Integers
ORB_REQUEST_TIMEOUT=300                # Converted to int(300)
ORB_MAX_MACHINES_PER_REQUEST=50        # Converted to int(50)

# Floats
ORB_RETRY__BACKOFF_MULTIPLIER=2.5      # Converted to float(2.5)
```

### Environment Variable Examples

#### Development Environment
```bash
#!/bin/bash
# Development environment setup
export ORB_ENVIRONMENT=development
export ORB_LOG_LEVEL=DEBUG
export ORB_DEBUG=true

# AWS development account
export ORB_AWS_REGION=us-east-1
export ORB_AWS_PROFILE=development
export ORB_AWS_SUBNET_IDS='["subnet-dev123"]'
export ORB_AWS_SECURITY_GROUP_IDS='["sg-dev456"]'

# Relaxed timeouts for development
export ORB_REQUEST_TIMEOUT=600
export ORB_AWS_MAX_RETRIES=3
```

#### Production Environment
```bash
#!/bin/bash
# Production environment setup
export ORB_ENVIRONMENT=production
export ORB_LOG_LEVEL=INFO
export ORB_DEBUG=false

# AWS production account with IAM role
export ORB_AWS_REGION=us-east-1
export ORB_AWS_ROLE_ARN=arn:aws:iam::123456789012:role/OrbitProductionRole
export ORB_AWS_SUBNET_IDS='["subnet-prod123", "subnet-prod456", "subnet-prod789"]'
export ORB_AWS_SECURITY_GROUP_IDS='["sg-prod123", "sg-prod456"]'
export ORB_AWS_KEY_NAME=production-key-pair

# Production performance settings
export ORB_REQUEST_TIMEOUT=300
export ORB_MAX_MACHINES_PER_REQUEST=100
export ORB_AWS_MAX_RETRIES=5

# Circuit breaker for resilience
export ORB_CIRCUIT_BREAKER__ENABLED=true
export ORB_CIRCUIT_BREAKER__FAILURE_THRESHOLD=5
export ORB_CIRCUIT_BREAKER__RECOVERY_TIMEOUT=60
```

### Testing Environment Variables

Verify environment variable configuration:

```bash
# Test configuration loading
orb system health --verbose

# Validate specific provider configuration
orb providers health aws_production_us-east-1

# Test environment variable precedence
ORB_LOG_LEVEL=DEBUG orb system health

# Validate AWS credentials and configuration
ORB_AWS_REGION=us-west-2 orb providers test aws
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
orb config validate
```

#### Environment Variable Issues
```bash
# Check environment variables
env | grep HF_

# Test environment override
ORB_LOG_LEVEL=DEBUG orb config validate
```

### Configuration Debugging

Enable debug logging to see configuration loading:

```bash
export ORB_LOG_LEVEL=DEBUG
orb system serve --config config.json
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

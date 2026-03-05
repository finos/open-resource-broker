# Environment Variables Reference

Open Resource Broker supports comprehensive environment variable configuration for all settings, enabling flexible deployment across different environments without modifying configuration files.

## Variable Naming Convention

All ORB environment variables follow the pattern:
- **Core settings**: `ORB_<SETTING_NAME>`
- **Provider settings**: `ORB_<PROVIDER>_<SETTING_NAME>`
- **Nested settings**: `ORB_<SECTION>__<SUBSECTION>_<SETTING>`

## Precedence Order

Environment variables override configuration file values:

1. **Environment variables** (highest precedence)
2. **Configuration file** (`config/app_config.json`)
3. **Default values** (lowest precedence)

## Core Application Variables

### Basic Configuration
```bash
# Application environment
ORB_ENVIRONMENT=production
ORB_DEBUG=false
ORB_LOG_LEVEL=INFO

# Request handling
ORB_REQUEST_TIMEOUT=300
ORB_MAX_MACHINES_PER_REQUEST=100

# Directory overrides
ORB_CONFIG_DIR=/opt/orb/config
ORB_WORK_DIR=/opt/orb/work
ORB_LOG_DIR=/opt/orb/logs
```

### Logging Configuration
```bash
# Console logging (for HostFactory integration)
ORB_LOGGING_CONSOLE_ENABLED=true
ORB_LOGGING_FILE_ENABLED=false
ORB_LOGGING_FORMAT=json
```

## AWS Provider Variables

### Authentication & Region
```bash
# Basic AWS configuration
ORB_AWS_REGION=us-west-2
ORB_AWS_PROFILE=production

# IAM role assumption
ORB_AWS_ROLE_ARN=arn:aws:iam::123456789012:role/OrbitRole
ORB_AWS_EXTERNAL_ID=unique-external-id

# Direct credentials (not recommended for production)
ORB_AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
ORB_AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
ORB_AWS_SESSION_TOKEN=temporary-session-token
```

### Service Configuration
```bash
# API endpoints
ORB_AWS_ENDPOINT_URL=https://ec2.us-west-2.amazonaws.com
ORB_AWS_STS_ENDPOINT_URL=https://sts.us-west-2.amazonaws.com

# Retry configuration
ORB_AWS_MAX_RETRIES=3
ORB_AWS_RETRY_MODE=adaptive

# Request timeouts
ORB_AWS_CONNECT_TIMEOUT=60
ORB_AWS_READ_TIMEOUT=300
```

### Infrastructure Defaults
```bash
# Network configuration
ORB_AWS_SUBNET_IDS='["subnet-12345", "subnet-67890"]'
ORB_AWS_SECURITY_GROUP_IDS='["sg-abcdef"]'
ORB_AWS_KEY_NAME=my-keypair

# Instance configuration
ORB_AWS_INSTANCE_TYPE=t3.medium
ORB_AWS_IMAGE_ID=ami-0abcdef1234567890
```

### Handler Configuration
```bash
# Enable/disable specific handlers
ORB_AWS_HANDLERS__RUNINSTANCES_ENABLED=true
ORB_AWS_HANDLERS__EC2FLEET_ENABLED=true
ORB_AWS_HANDLERS__SPOTFLEET_ENABLED=false
ORB_AWS_HANDLERS__ASG_ENABLED=true

# Handler-specific settings
ORB_AWS_HANDLERS__EC2FLEET_TARGET_CAPACITY_TYPE=on-demand
ORB_AWS_HANDLERS__SPOTFLEET_ALLOCATION_STRATEGY=diversified
```

### Launch Template Configuration
```bash
# Launch template settings (JSON format)
ORB_AWS_LAUNCH_TEMPLATE='{"LaunchTemplateName": "orb-template", "Version": "$Latest"}'

# Network interfaces (JSON format)
ORB_AWS_LAUNCH_TEMPLATE_NETWORK_INTERFACES='[{
  "DeviceIndex": 0,
  "SubnetId": "subnet-12345",
  "Groups": ["sg-abcdef"],
  "AssociatePublicIpAddress": true
}]'
```

## HostFactory Integration Variables

When using HostFactory scheduler, these variables provide compatibility:

```bash
# Directory overrides (HostFactory standard)
HF_PROVIDER_CONFDIR=/opt/symphony/hostfactory/conf
HF_PROVIDER_WORKDIR=/opt/symphony/hostfactory/work
HF_PROVIDER_LOGDIR=/opt/symphony/hostfactory/logs

# Logging control
HF_LOGGING_CONSOLE_ENABLED=false
HF_LOGLEVEL=INFO

# Timeout overrides
HF_PROVIDER_ACTION_TIMEOUT=600
```

## Complex Configuration Examples

### Multi-Environment Setup
```bash
# Development environment
export ORB_ENVIRONMENT=development
export ORB_DEBUG=true
export ORB_AWS_REGION=us-east-1
export ORB_AWS_PROFILE=dev
export ORB_AWS_INSTANCE_TYPE=t3.micro

# Production environment
export ORB_ENVIRONMENT=production
export ORB_DEBUG=false
export ORB_AWS_REGION=us-west-2
export ORB_AWS_PROFILE=prod
export ORB_AWS_ROLE_ARN=arn:aws:iam::123456789012:role/ProdOrbitRole
export ORB_AWS_INSTANCE_TYPE=m5.large
```

### Cross-Region Configuration
```bash
# Primary region
export ORB_AWS_REGION=us-east-1
export ORB_AWS_SUBNET_IDS='["subnet-east-1", "subnet-east-2"]'
export ORB_AWS_SECURITY_GROUP_IDS='["sg-east-web"]'

# Failover region (separate provider instance)
export ORB_AWS_WEST_REGION=us-west-2
export ORB_AWS_WEST_SUBNET_IDS='["subnet-west-1", "subnet-west-2"]'
export ORB_AWS_WEST_SECURITY_GROUP_IDS='["sg-west-web"]'
```

### JSON Array and Object Values

For complex nested configurations, use JSON format:

```bash
# Array values
export ORB_AWS_SUBNET_IDS='["subnet-12345", "subnet-67890", "subnet-abcdef"]'
export ORB_AWS_SECURITY_GROUP_IDS='["sg-web", "sg-app", "sg-db"]'

# Object values
export ORB_AWS_LAUNCH_TEMPLATE='{
  "LaunchTemplateName": "orb-production",
  "Version": "2"
}'

# Complex nested objects
export ORB_AWS_HANDLERS='{
  "runinstances": {"enabled": true, "priority": 1},
  "ec2fleet": {"enabled": true, "priority": 2},
  "spotfleet": {"enabled": false},
  "asg": {"enabled": true, "priority": 3}
}'
```

## Validation and Type Conversion

ORB automatically validates and converts environment variable values:

- **Strings**: Used as-is
- **Integers**: `ORB_REQUEST_TIMEOUT=300`
- **Booleans**: `true`, `false`, `1`, `0`, `yes`, `no`
- **Arrays**: JSON format `'["item1", "item2"]'`
- **Objects**: JSON format `'{"key": "value"}'`

## Environment File Support

Create `.env` file in your configuration directory:

```bash
# .env file
ORB_ENVIRONMENT=production
ORB_DEBUG=false
ORB_AWS_REGION=us-west-2
ORB_AWS_PROFILE=prod
ORB_AWS_SUBNET_IDS=["subnet-12345", "subnet-67890"]
```

Load with:
```bash
# Load environment file
source .env

# Or use with Docker
docker run --env-file .env orb-py:latest
```

## Security Best Practices

### Recommended Approach
```bash
# Use IAM roles (recommended)
export ORB_AWS_ROLE_ARN=arn:aws:iam::123456789012:role/OrbitRole

# Use AWS profiles (recommended)
export ORB_AWS_PROFILE=production
```

### Avoid in Production
```bash
# Don't use direct credentials in production
export ORB_AWS_ACCESS_KEY_ID=...  # Avoid
export ORB_AWS_SECRET_ACCESS_KEY=...  # Avoid
```

### Secure Storage
- Use AWS Systems Manager Parameter Store
- Use HashiCorp Vault
- Use Kubernetes secrets
- Use Docker secrets

## Troubleshooting

### Verify Environment Variables
```bash
# List all ORB variables
env | grep ORB_

# Test configuration loading
orb system health --verbose

# Validate AWS configuration
orb providers health aws
```

### Common Issues

**Invalid JSON format:**
```bash
# Wrong
export ORB_AWS_SUBNET_IDS=["subnet-123"]  # Missing quotes

# Correct
export ORB_AWS_SUBNET_IDS='["subnet-123"]'  # Quoted JSON
```

**Type conversion errors:**
```bash
# Wrong
export ORB_DEBUG=True  # Python boolean

# Correct
export ORB_DEBUG=true  # JSON boolean
```

**Nested delimiter confusion:**
```bash
# Wrong
export ORB_HANDLERS_RUNINSTANCES_ENABLED=true  # Single underscore

# Correct
export ORB_AWS_HANDLERS__RUNINSTANCES_ENABLED=true  # Double underscore
```
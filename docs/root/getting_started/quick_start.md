# Quick Start Guide

Get up and running with the Open Host Factory Plugin in minutes. This guide covers the essential steps to start provisioning cloud resources through IBM Spectrum Symphony Host Factory.

## Prerequisites

Before starting, ensure you have:

- Python 3.8 or higher installed
- AWS CLI configured or IAM role with appropriate permissions
- Basic familiarity with command-line tools

## Installation

### Option 1: Docker (Recommended)

```bash
# Pull and run the plugin
docker run -it --rm \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd)/config:/app/config \
  your-registry/open-hostfactory-plugin:latest \
  getAvailableTemplates
```

### Option 2: Local Installation

```bash
# Clone and install
git clone <repository-url>
cd open-hostfactory-plugin
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Basic Configuration

Create a minimal configuration file at `config/config.json`:

```json
{
  "version": "2.0.0",
  "provider": {
    "active_provider": "aws-default",
    "selection_policy": "FIRST_AVAILABLE",
    "providers": [
      {
        "name": "aws-default",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "config": {
          "region": "us-east-1",
          "profile": "default"
        }
      }
    ]
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
  "logging": {
    "level": "INFO",
    "file_path": "logs/app.log",
    "console_enabled": true
  }
}
```

## First Commands

### 1. List Available Templates

```bash
# Using Python directly
python src/run.py templates list

# Using shell script (HostFactory integration)
./scripts/getAvailableTemplates.sh
```

Expected output:
```json
{
  "templates": [
    {
      "templateId": "basic-template",
      "maxNumber": 10,
      "attributes": {
        "type": ["String", "X86_64"],
        "ncpus": ["Numeric", "2"],
        "nram": ["Numeric", "4096"]
      }
    }
  ]
}
```

### 2. Request Machines

```bash
# Request 2 machines using basic template
python src/run.py machines create basic-template 2

# Using shell script
echo '{"templateId": "basic-template", "maxNumber": 2}' | \
  ./scripts/requestMachines.sh -f -
```

Expected output:
```json
{
  "requestId": "req-12345",
  "machines": [
    {
      "machineId": "i-0123456789abcdef0",
      "status": "pending"
    },
    {
      "machineId": "i-0987654321fedcba0", 
      "status": "pending"
    }
  ]
}
```

### 3. Check Request Status

```bash
# Check status of your request
python run.py getRequestStatus --request-id req-12345

# Using shell script
echo '{"requestId": "req-12345"}' | \
  ./scripts/getRequestStatus.sh
```

### 4. Return Machines

```bash
# Return machines when done
python run.py requestReturnMachines --request-id req-12345

# Using shell script
echo '{"requestId": "req-12345"}' | \
  ./scripts/requestReturnMachines.sh
```

## Common Use Cases

### Scenario 1: Development Environment

```bash
# Quick development setup
python run.py requestMachines \
  --template-id dev-template \
  --max-number 1 \
  --attributes '{"environment": "development"}'
```

### Scenario 2: Batch Processing

```bash
# Request multiple machines for batch job
python run.py requestMachines \
  --template-id batch-template \
  --max-number 10 \
  --attributes '{"job_type": "batch", "priority": "high"}'
```

### Scenario 3: Auto Scaling

```bash
# Use auto scaling template
python run.py requestMachines \
  --template-id autoscale-template \
  --max-number 5 \
  --attributes '{"min_size": 2, "max_size": 10}'
```

## Verification

### Health Check

```bash
# Verify plugin health
python run.py --health-check

# Check provider connectivity
python run.py --validate-provider
```

### Configuration Validation

```bash
# Validate configuration
python run.py --validate-config

# Test AWS connectivity
aws sts get-caller-identity
```

## Integration with HostFactory

### Shell Script Integration

The plugin provides shell scripts that integrate with IBM Spectrum Symphony Host Factory:

```bash
# HostFactory calls these scripts
./scripts/getAvailableTemplates.sh
./scripts/requestMachines.sh -f /tmp/input_file
./scripts/requestReturnMachines.sh -f /tmp/input_file
./scripts/getRequestStatus.sh -f /tmp/input_file
```

### HostFactory Configuration

Add to your HostFactory configuration:

```xml
<HostProvider name="aws-provider">
  <Script>
    <GetAvailableTemplates>/path/to/getAvailableTemplates.sh</GetAvailableTemplates>
    <RequestMachines>/path/to/requestMachines.sh</RequestMachines>
    <RequestReturnMachines>/path/to/requestReturnMachines.sh</RequestReturnMachines>
    <GetRequestStatus>/path/to/getRequestStatus.sh</GetRequestStatus>
  </Script>
</HostProvider>
```

## Troubleshooting

### Common Issues

#### AWS Permissions
```bash
# Check AWS permissions
aws ec2 describe-instances --max-items 1
```

#### Configuration Issues
```bash
# Validate YAML syntax
python -c "import yaml; yaml.safe_load(open('config/config.yml'))"
```

#### Python Dependencies
```bash
# Reinstall dependencies
pip install --force-reinstall -r requirements.txt
```

### Getting Help

- **Configuration Issues**: See [Configuration Guide](../user_guide/configuration.md)
- **Installation Problems**: See [Installation Guide](../user_guide/installation.md)
- **API Usage**: See [API Reference](../user_guide/api_reference.md)
- **Troubleshooting**: See [Troubleshooting Guide](../user_guide/troubleshooting.md)

## Next Steps

Now that you have the plugin running:

1. **Explore Templates**: Learn about [Template Management](../user_guide/templates.md)
2. **Advanced Configuration**: Review the [Configuration Guide](../user_guide/configuration.md)
3. **Production Deployment**: Follow the [Deployment Guide](../deployment/readme.md)
4. **Monitoring Setup**: Configure [Monitoring](../user_guide/monitoring.md)
5. **Development**: Read the [Developer Guide](../developer_guide/architecture.md)

## Example Workflows

### Complete Machine Lifecycle

```bash
# 1. List available templates
python run.py getAvailableTemplates

# 2. Request machines
REQUEST_ID=$(python run.py requestMachines \
  --template-id basic-template \
  --max-number 2 \
  --format json | jq -r '.requestId')

# 3. Monitor status
while true; do
  STATUS=$(python run.py getRequestStatus \
    --request-id $REQUEST_ID \
    --format json | jq -r '.status')
  echo "Status: $STATUS"
  [[ "$STATUS" == "completed" ]] && break
  sleep 30
done

# 4. Use machines for your workload
echo "Machines ready for use!"

# 5. Return machines when done
python run.py requestReturnMachines --request-id $REQUEST_ID
```

This quick start guide gets you up and running quickly. For detailed information on any topic, refer to the comprehensive guides in the documentation.

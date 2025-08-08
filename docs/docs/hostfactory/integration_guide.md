# HostFactory Integration Guide

This guide covers the integration of the Open Host Factory Plugin with IBM Spectrum Symphony Host Factory, including configuration, deployment, and operational considerations.

## Overview

The Open Host Factory Plugin integrates with IBM Spectrum Symphony Host Factory through shell script interfaces that conform to the HostFactory API specification. The plugin acts as a bridge between HostFactory and cloud providers, enabling dynamic provisioning of compute resources.

## Integration Architecture

### Component Interaction

```
IBM Spectrum Symphony Host Factory
           |
           | (Shell Script Calls)
           v
Open Host Factory Plugin Shell Scripts
           |
           | (Python Execution)
           v
Open Host Factory Plugin Core
           |
           | (API Calls)
           v
Cloud Provider (AWS)
```

### Shell Script Interface

The plugin provides four main shell scripts that implement the HostFactory API:

- `getAvailableTemplates.sh` - Lists available machine templates
- `requestMachines.sh` - Requests provisioning of machines
- `requestReturnMachines.sh` - Requests termination of machines
- `getRequestStatus.sh` - Checks status of provisioning requests

## HostFactory Configuration

### Basic HostFactory Setup

Add the plugin to your HostFactory configuration file:

```xml
<HostProvider name="aws-cloud-provider">
  <Script>
    <GetAvailableTemplates>/opt/hostfactory-plugin/scripts/getAvailableTemplates.sh</GetAvailableTemplates>
    <RequestMachines>/opt/hostfactory-plugin/scripts/requestMachines.sh</RequestMachines>
    <RequestReturnMachines>/opt/hostfactory-plugin/scripts/requestReturnMachines.sh</RequestReturnMachines>
    <GetRequestStatus>/opt/hostfactory-plugin/scripts/getRequestStatus.sh</GetRequestStatus>
  </Script>
  <Parameters>
    <Parameter name="CONFIG_PATH">/opt/hostfactory-plugin/config/config.json</Parameter>
    <Parameter name="LOG_LEVEL">INFO</Parameter>
  </Parameters>
</HostProvider>
```

### Advanced HostFactory Configuration

For production environments with multiple providers:

```xml
<HostProviders>
  <HostProvider name="aws-us-east-1">
    <Script>
      <GetAvailableTemplates>/opt/hostfactory-plugin/scripts/getAvailableTemplates.sh</GetAvailableTemplates>
      <RequestMachines>/opt/hostfactory-plugin/scripts/requestMachines.sh</RequestMachines>
      <RequestReturnMachines>/opt/hostfactory-plugin/scripts/requestReturnMachines.sh</RequestReturnMachines>
      <GetRequestStatus>/opt/hostfactory-plugin/scripts/getRequestStatus.sh</GetRequestStatus>
    </Script>
    <Parameters>
      <Parameter name="CONFIG_PATH">/opt/hostfactory-plugin/config/aws-us-east-1.yml</Parameter>
      <Parameter name="AWS_REGION">us-east-1</Parameter>
      <Parameter name="PROVIDER_NAME">aws-us-east-1</Parameter>
    </Parameters>
  </HostProvider>

  <HostProvider name="aws-us-west-2">
    <Script>
      <GetAvailableTemplates>/opt/hostfactory-plugin/scripts/getAvailableTemplates.sh</GetAvailableTemplates>
      <RequestMachines>/opt/hostfactory-plugin/scripts/requestMachines.sh</RequestMachines>
      <RequestReturnMachines>/opt/hostfactory-plugin/scripts/requestReturnMachines.sh</RequestReturnMachines>
      <GetRequestStatus>/opt/hostfactory-plugin/scripts/getRequestStatus.sh</GetRequestStatus>
    </Script>
    <Parameters>
      <Parameter name="CONFIG_PATH">/opt/hostfactory-plugin/config/aws-us-west-2.yml</Parameter>
      <Parameter name="AWS_REGION">us-west-2</Parameter>
      <Parameter name="PROVIDER_NAME">aws-us-west-2</Parameter>
    </Parameters>
  </HostProvider>
</HostProviders>
```

## API Compliance

### getAvailableTemplates

**HostFactory Call:**
```bash
/path/to/getAvailableTemplates.sh
```

**Expected Output Format:**
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

### requestMachines

**HostFactory Call:**
```bash
/path/to/requestMachines.sh -f /tmp/input_file
```

**Input Format:**
```json
{
  "templateId": "basic-template",
  "maxNumber": 5,
  "attributes": {
    "priority": "high",
    "environment": "production"
  }
}
```

**Expected Output Format:**
```json
{
  "requestId": "req-12345",
  "machines": [
    {
      "machineId": "i-0123456789abcdef0",
      "status": "pending",
      "attributes": {
        "type": ["String", "X86_64"],
        "ncpus": ["Numeric", "2"],
        "nram": ["Numeric", "4096"]
      }
    }
  ]
}
```

### requestReturnMachines

**HostFactory Call:**
```bash
/path/to/requestReturnMachines.sh -f /tmp/input_file
```

**Input Format:**
```json
{
  "requestId": "req-12345"
}
```

**Expected Output Format:**
```json
{
  "requestId": "req-12345",
  "status": "terminating",
  "machines": [
    {
      "machineId": "i-0123456789abcdef0",
      "status": "terminating"
    }
  ]
}
```

### getRequestStatus

**HostFactory Call:**
```bash
/path/to/getRequestStatus.sh -f /tmp/input_file
```

**Input Format:**
```json
{
  "requestId": "req-12345"
}
```

**Expected Output Format:**
```json
{
  "requestId": "req-12345",
  "status": "completed",
  "machines": [
    {
      "machineId": "i-0123456789abcdef0",
      "status": "running",
      "ipAddress": "10.0.1.100",
      "attributes": {
        "type": ["String", "X86_64"],
        "ncpus": ["Numeric", "2"],
        "nram": ["Numeric", "4096"]
      }
    }
  ]
}
```

## Deployment Scenarios

### Scenario 1: Single Region Deployment

For simple deployments with one AWS region:

```yaml
# config/config.yml
provider:
  type: aws
  aws:
    region: us-east-1
    profile: default

storage:
  type: dynamodb
  dynamodb:
    table_prefix: hostfactory
    region: us-east-1

templates:
  - template_id: basic-template
    max_number: 10
    attributes:
      vm_type: t3.medium
      image_id: ami-0abcdef1234567890
```

### Scenario 2: Multi-Region Deployment

For deployments across multiple AWS regions:

```yaml
# config/aws-us-east-1.yml
provider:
  type: aws
  aws:
    region: us-east-1
    profile: default

storage:
  type: dynamodb
  dynamodb:
    table_prefix: hostfactory-east
    region: us-east-1

# config/aws-us-west-2.yml
provider:
  type: aws
  aws:
    region: us-west-2
    profile: default

storage:
  type: dynamodb
  dynamodb:
    table_prefix: hostfactory-west
    region: us-west-2
```

### Scenario 3: High Availability Deployment

For production environments requiring high availability:

```yaml
# config/production.yml
provider:
  type: aws
  aws:
    region: us-east-1
    profile: production
    max_retries: 5
    timeout: 60

storage:
  type: dynamodb
  dynamodb:
    table_prefix: hostfactory-prod
    region: us-east-1
    backup_enabled: true

logging:
  level: INFO
  file_path: /var/log/hostfactory-plugin/app.log
  max_size: 100MB
  backup_count: 10

monitoring:
  enabled: true
  metrics_endpoint: http://monitoring.internal:8080/metrics
```

## Security Configuration

### IAM Permissions

The plugin requires specific IAM permissions to function properly:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstances",
        "ec2:DescribeImages",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "ec2:CreateTags"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "autoscaling:CreateAutoScalingGroup",
        "autoscaling:DeleteAutoScalingGroup",
        "autoscaling:DescribeAutoScalingGroups",
        "autoscaling:UpdateAutoScalingGroup"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/hostfactory-*"
    }
  ]
}
```

### Network Security

Configure security groups for provisioned instances:

```yaml
# In template configuration
templates:
  - template_id: secure-template
    attributes:
      security_group_ids:
        - sg-12345678  # Allow SSH from management network
        - sg-87654321  # Allow application traffic
      subnet_ids:
        - subnet-abcdef12  # Private subnet
```

## Monitoring and Logging

### HostFactory Integration Monitoring

Monitor the integration through HostFactory logs and plugin logs:

```bash
# HostFactory logs
tail -f /opt/symphony/logs/hostfactory.log

# Plugin logs
tail -f /var/log/hostfactory-plugin/app.log

# AWS CloudWatch logs (if configured)
aws logs tail /aws/lambda/hostfactory-plugin --follow
```

### Health Checks

Implement health checks for the integration:

```bash
# Test plugin health
/opt/hostfactory-plugin/scripts/getAvailableTemplates.sh

# Test AWS connectivity
aws sts get-caller-identity

# Test DynamoDB connectivity
aws dynamodb describe-table --table-name hostfactory-requests
```

## Troubleshooting

### Common Integration Issues

#### Script Execution Permissions
```bash
# Ensure scripts are executable
chmod +x /opt/hostfactory-plugin/scripts/*.sh
```

#### Configuration Path Issues
```bash
# Verify configuration file exists and is readable
ls -la /opt/hostfactory-plugin/config/config.yml
```

#### AWS Credentials Issues
```bash
# Test AWS credentials
aws sts get-caller-identity
```

### Debugging HostFactory Calls

Enable debug logging to troubleshoot integration issues:

```yaml
# config/config.yml
logging:
  level: DEBUG
  console_enabled: true
  file_path: /var/log/hostfactory-plugin/debug.log
```

### Error Handling

The plugin provides structured error responses for HostFactory:

```json
{
  "error": {
    "code": "TEMPLATE_NOT_FOUND",
    "message": "Template 'invalid-template' not found",
    "details": {
      "available_templates": ["basic-template", "advanced-template"]
    }
  }
}
```

## Performance Optimization

### Caching

Enable template and configuration caching:

```yaml
# config/config.yml
caching:
  enabled: true
  template_cache_ttl: 300  # 5 minutes
  config_cache_ttl: 600    # 10 minutes
```

### Connection Pooling

Configure AWS connection pooling:

```yaml
# config/config.yml
provider:
  aws:
    connection_pool_size: 10
    max_retries: 3
    timeout: 30
```

## Best Practices

### Configuration Management
- Use separate configuration files for different environments
- Store sensitive information in environment variables or AWS Secrets Manager
- Validate configuration before deployment

### Monitoring
- Monitor HostFactory integration logs
- Set up CloudWatch alarms for AWS resource usage
- Track plugin performance metrics

### Security
- Use IAM roles instead of access keys when possible
- Regularly rotate credentials
- Implement least privilege access policies

### Maintenance
- Regularly update the plugin to the latest version
- Monitor AWS service limits and quotas
- Implement automated backup and recovery procedures

This integration guide provides comprehensive coverage of integrating the Open Host Factory Plugin with IBM Spectrum Symphony Host Factory. For specific deployment scenarios or troubleshooting, refer to the related documentation sections.

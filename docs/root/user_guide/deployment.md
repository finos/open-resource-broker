# Deployment Guide

This guide covers deploying the Open Host Factory Plugin in production environments with IBM Spectrum Symphony.

## Overview

The Open Host Factory Plugin is deployed as a **command-line tool** that IBM Spectrum Symphony Host Factory calls to manage cloud resources. This guide covers production deployment scenarios and best practices.

## Deployment Architecture

### Symphony Integration Model

```
IBM Spectrum Symphony
        v
   Host Factory
        v
  Plugin Script (run.py)
        v
   Cloud Provider (AWS)
```

The plugin operates as:
- **Command-line script** called by Symphony Host Factory
- **Stateless operations** with persistent data storage
- **Direct cloud provider integration** (AWS APIs)

## Production Deployment

### Environment Setup

#### Server Requirements
- **OS**: Linux (RHEL/CentOS 7+, Ubuntu 18.04+)
- **Python**: 3.8 or higher
- **Memory**: 2GB RAM minimum, 4GB recommended
- **Storage**: 10GB available space
- **Network**: Outbound HTTPS access to AWS APIs

#### User Account Setup
```bash
# Create dedicated user for the plugin
sudo useradd -m -s /bin/bash hostfactory
sudo usermod -aG wheel hostfactory  # For sudo access if needed

# Switch to the user
sudo su - hostfactory
```

### Application Deployment

#### Install the Application
```bash
# Clone to production location
cd /opt
sudo git clone <repository-url> hostfactory-plugin
sudo chown -R hostfactory:hostfactory hostfactory-plugin

# Switch to application user
sudo su - hostfactory
cd /opt/hostfactory-plugin

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

#### Directory Structure
```bash
# Create production directories
sudo mkdir -p /var/log/hostfactory
sudo mkdir -p /var/lib/hostfactory/data
sudo mkdir -p /etc/hostfactory

# Set permissions
sudo chown -R hostfactory:hostfactory /var/log/hostfactory
sudo chown -R hostfactory:hostfactory /var/lib/hostfactory
sudo chown -R hostfactory:hostfactory /etc/hostfactory
```

### Configuration

#### Production Configuration
Create `/etc/hostfactory/config.json`:

```json
{
  "provider": {
    "type": "aws",
    "aws": {
      "region": "us-east-1",
      "profile": "production"
    }
  },
  "logging": {
    "level": "INFO",
    "file_path": "/var/log/hostfactory/app.log",
    "console_enabled": false,
    "rotation": {
      "enabled": true,
      "max_size": "100MB",
      "backup_count": 10
    }
  },
  "storage": {
    "strategy": "json",
    "json_strategy": {
      "storage_type": "single_file",
      "base_path": "/var/lib/hostfactory/data",
      "filenames": {
        "single_file": "request_database.json"
      }
    }
  },
  "template": {
    "default_image_id": "ami-0abcdef1234567890",
    "default_instance_type": "t3.medium",
    "subnet_ids": ["subnet-12345678", "subnet-87654321"],
    "security_group_ids": ["sg-12345678"],
    "default_key_name": "production-key",
    "default_max_number": 50
  },
  "environment": "production",
  "debug": false,
  "request_timeout": 600,
  "max_machines_per_request": 100
}
```

#### AWS Credentials Setup
```bash
# Configure AWS CLI for production user
sudo su - hostfactory
aws configure --profile production
# Enter production AWS credentials

# Or use IAM role (recommended for EC2 deployment)
# Attach IAM role to EC2 instance with required permissions
```

#### Log Rotation Setup
Create `/etc/logrotate.d/hostfactory`:

```
/var/log/hostfactory/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 hostfactory hostfactory
    postrotate
        # Signal application to reopen log files if needed
    endscript
}
```

### Symphony Integration

#### Configure Symphony Host Factory

Edit Symphony's `hostfactory.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<HostFactory>
  <Providers>
    <Provider name="aws-production">
      <Command>/opt/hostfactory-plugin/.venv/bin/python</Command>
      <Arguments>/opt/hostfactory-plugin/run.py</Arguments>
      <WorkingDirectory>/opt/hostfactory-plugin</WorkingDirectory>
      <Environment>
        <Variable name="HOSTFACTORY_CONFIG" value="/etc/hostfactory/config.json"/>
        <Variable name="AWS_PROFILE" value="production"/>
        <Variable name="PYTHONPATH" value="/opt/hostfactory-plugin"/>
      </Environment>
      <Timeout>600</Timeout>
      <MaxConcurrentRequests>10</MaxConcurrentRequests>
    </Provider>
  </Providers>
</HostFactory>
```

#### Test Symphony Integration
```bash
# Test from Symphony command line
sym hostfactory -provider aws-production -cmd getAvailableTemplates

# Test machine request
sym hostfactory -provider aws-production -cmd requestMachines -data '{"template_id": "template-1", "machine_count": 1}'
```

## Security Configuration

### File Permissions
```bash
# Set secure permissions
sudo chmod 750 /opt/hostfactory-plugin
sudo chmod 640 /etc/hostfactory/config.json
sudo chmod 755 /opt/hostfactory-plugin/run.py

# Ensure logs are readable by Symphony
sudo chmod 644 /var/log/hostfactory/app.log
```

### AWS Security
- Use IAM roles instead of access keys when possible
- Follow principle of least privilege for IAM permissions
- Enable CloudTrail for audit logging
- Use VPC endpoints for AWS API calls if available

### Network Security
- Restrict outbound access to required AWS endpoints only
- Use security groups to limit access to Symphony servers
- Consider using AWS PrivateLink for API access

## Monitoring and Maintenance

### Health Checks
Create a health check script `/opt/hostfactory-plugin/health_check.sh`:

```bash
#!/bin/bash
cd /opt/hostfactory-plugin
source .venv/bin/activate

# Test basic functionality
python run.py getAvailableTemplates > /dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "OK: Plugin is healthy"
    exit 0
else
    echo "ERROR: Plugin health check failed"
    exit 1
fi
```

### Monitoring Scripts
```bash
# Check disk space
df -h /var/lib/hostfactory /var/log/hostfactory

# Check log file size
ls -lh /var/log/hostfactory/app.log

# Monitor active requests
python /opt/hostfactory-plugin/run.py getReturnRequests --active-only
```

### Backup Strategy
```bash
# Backup configuration
cp /etc/hostfactory/config.json /backup/config-$(date +%Y%m%d).json

# Backup data
cp /var/lib/hostfactory/data/request_database.json /backup/data-$(date +%Y%m%d).json

# Backup logs (optional)
tar -czf /backup/logs-$(date +%Y%m%d).tar.gz /var/log/hostfactory/
```

## Performance Tuning

### Configuration Optimization
```json
{
  "storage": {
    "strategy": "sqlite",
    "sql_strategy": {
      "type": "sqlite",
      "name": "/var/lib/hostfactory/data/database.db",
      "pool_size": 10,
      "timeout": 30
    }
  },
  "request_timeout": 300,
  "max_machines_per_request": 50
}
```

### System Optimization
```bash
# Increase file descriptor limits
echo "hostfactory soft nofile 65536" >> /etc/security/limits.conf
echo "hostfactory hard nofile 65536" >> /etc/security/limits.conf

# Optimize Python performance
export PYTHONOPTIMIZE=1
export PYTHONDONTWRITEBYTECODE=1
```

## Troubleshooting

### Common Issues

#### Permission Errors
```bash
# Check file permissions
ls -la /opt/hostfactory-plugin/run.py
ls -la /etc/hostfactory/config.json

# Fix permissions if needed
sudo chown hostfactory:hostfactory /opt/hostfactory-plugin/run.py
sudo chmod 755 /opt/hostfactory-plugin/run.py
```

#### AWS Credential Issues
```bash
# Test AWS credentials
sudo su - hostfactory
aws sts get-caller-identity --profile production

# Check IAM permissions
aws iam get-user --profile production
```

#### Symphony Integration Issues
```bash
# Check Symphony logs
tail -f /opt/symphony/logs/hostfactory.log

# Test plugin directly
cd /opt/hostfactory-plugin
source .venv/bin/activate
python run.py getAvailableTemplates
```

### Log Analysis
```bash
# Check application logs
tail -f /var/log/hostfactory/app.log

# Search for errors
grep ERROR /var/log/hostfactory/app.log

# Monitor real-time activity
tail -f /var/log/hostfactory/app.log | grep -E "(REQUEST|ERROR|WARN)"
```

## Disaster Recovery

### Backup Procedures
1. **Configuration Backup**: Daily backup of configuration files
2. **Data Backup**: Regular backup of request database
3. **Log Backup**: Weekly backup of log files for audit

### Recovery Procedures
1. **Application Recovery**: Restore from Git repository
2. **Configuration Recovery**: Restore from configuration backup
3. **Data Recovery**: Restore request database from backup

### Testing Recovery
```bash
# Test configuration restore
cp /backup/config-20250630.json /etc/hostfactory/config.json

# Test data restore
cp /backup/data-20250630.json /var/lib/hostfactory/data/request_database.json

# Verify functionality
python /opt/hostfactory-plugin/run.py getAvailableTemplates
```

## Scaling Considerations

### Horizontal Scaling
- Deploy multiple instances for different Symphony clusters
- Use separate AWS accounts/regions for isolation
- Implement request routing based on workload

### Vertical Scaling
- Increase server resources for high-volume environments
- Optimize database performance for large request volumes
- Consider switching to PostgreSQL for high concurrency

## Next Steps

- **[Configuration](configuration.md)**: Advanced configuration options
- **[Monitoring](monitoring.md)**: Set up monitoring and alerting
- **[Troubleshooting](troubleshooting.md)**: Diagnose and fix issues
- **[API Reference](api_reference.md)**: Command-line interface reference

# Troubleshooting

This guide helps you diagnose and resolve common issues with the Open Host Factory Plugin.

## Common Issues

### Installation Issues

#### Python Version Compatibility
```bash
# Check Python version
python --version

# Should be 3.8 or higher
# If not, install a compatible version
```

#### Dependency Installation Failures
```bash
# Clear pip cache
pip cache purge

# Upgrade pip
pip install --upgrade pip

# Install with verbose output
pip install -r requirements.txt -v
```

#### Permission Errors
```bash
# Use virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate

# Or install with user flag
pip install --user -r requirements.txt
```

### Configuration Issues

#### Configuration File Not Found
```bash
# Check file exists
ls -la config/config.json

# Create from example
cp config/config.example.json config/config.json

# Verify configuration format
python -m json.tool config/config.json
```

#### Invalid Configuration Format
```bash
# Validate JSON syntax
python -c "import json; json.load(open('config/config.json'))"

# Check configuration schema
python -m src.infrastructure.config.validate_config config/config.json
```

#### Environment Variable Issues
```bash
# Check environment variables
env | grep HF_

# Set required variables
export HF_PROVIDER_CONFDIR=/path/to/config
export HF_PROVIDER_LOGDIR=/path/to/logs
```

### AWS Provider Issues

#### AWS Credentials Not Found
```bash
# Check AWS credentials
aws sts get-caller-identity

# Configure AWS credentials
aws configure

# Or set environment variables
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_DEFAULT_REGION=us-east-1
```

#### AWS Permission Errors
```bash
# Check IAM permissions
aws iam get-user

# Test EC2 permissions
aws ec2 describe-instances --max-items 1

# Required permissions:
# - ec2:DescribeInstances
# - ec2:RunInstances
# - ec2:TerminateInstances
# - ec2:DescribeImages
# - ec2:DescribeSubnets
# - ec2:DescribeSecurityGroups
```

#### AWS Region Issues
```bash
# Check current region
aws configure get region

# Set region
aws configure set region us-east-1

# Or in configuration file
{
  "aws": {
    "region": "us-east-1"
  }
}
```

### Database Issues

#### Database Connection Errors
```bash
# Check database file permissions
ls -la data/database.db

# Create data directory
mkdir -p data

# Initialize database
python -m src.infrastructure.persistence.database.init_db
```

#### Database Corruption
```bash
# Backup existing database
cp data/database.db data/database.db.backup

# Recreate database
rm data/database.db
python -m src.infrastructure.persistence.database.init_db

# Restore from backup if needed
cp data/database.db.backup data/database.db
```

#### JSON Storage Issues
```bash
# Check JSON file format
python -m json.tool data/database.json

# Fix JSON syntax errors
# Remove trailing commas, fix quotes, etc.

# Reset JSON storage
rm data/database.json
echo '{}' > data/database.json
```

### Runtime Issues

#### Application Won't Start
```bash
# Check for import errors
python -c "import src.bootstrap"

# Run with debug logging
python -m src.bootstrap --log-level DEBUG

# Check for port conflicts
netstat -an | grep :8080
```

#### Memory Issues
```bash
# Check memory usage
ps aux | grep python

# Monitor memory during execution
top -p $(pgrep -f "python.*bootstrap")

# Reduce memory usage by adjusting configuration
{
  "database": {
    "connection_pool_size": 5
  }
}
```

#### Performance Issues
```bash
# Profile application
python -m cProfile -o profile.stats -m src.bootstrap

# Check slow queries
tail -f logs/app.log | grep "slow query"

# Monitor system resources
htop
```

### Template Issues

#### Template Not Found
```bash
# List available templates
python -m src.api.handlers.template --list

# Check template configuration
cat config/templates.json

# Validate template format
python -m src.domain.template.validate_template template-id
```

#### Template Validation Errors
```bash
# Check template fields
python -c "
from src.infrastructure.config import get_config
config = get_config()
print(config.templates)
"

# Common required fields:
# - template_id
# - name
# - instance_type
# - image_id
# - provider_api
```

### Request Issues

#### Request Creation Failures
```bash
# Check request parameters
python -m src.api.handlers.request --validate-request request.json

# Common validation errors:
# - Invalid template_id
# - machine_count <= 0
# - Missing required fields
```

#### Request Status Not Updating
```bash
# Check event processing
tail -f logs/app.log | grep "event"

# Verify event handlers are registered
python -m src.infrastructure.events.debug --list-handlers

# Check provider connectivity
python -m src.providers.aws.test_connection
```

#### Machine Provisioning Failures
```bash
# Check AWS CloudTrail logs
aws logs describe-log-groups

# Check provider-specific errors
tail -f logs/app.log | grep "provider"

# Test provider operations manually
python -m src.providers.aws.test_operations
```

## Diagnostic Commands

### System Information
```bash
# Check system information
python -m src.infrastructure.diagnostics.system_info

# Check dependencies
pip list

# Check configuration
python -m src.infrastructure.diagnostics.config_info
```

### Health Checks
```bash
# Run all health checks
python -m src.infrastructure.diagnostics.health_check

# Check specific components
python -m src.infrastructure.diagnostics.health_check --component database
python -m src.infrastructure.diagnostics.health_check --component aws
python -m src.infrastructure.diagnostics.health_check --component events
```

### Debug Mode
```bash
# Enable debug mode
export HF_DEBUG=true

# Run with debug output
python -m src.bootstrap --debug

# Enable SQL query logging
export HF_SQL_DEBUG=true
```

## Log Analysis

### Log Locations
```bash
# Default log location
tail -f logs/app.log

# Check log configuration
grep -A 10 "logging" config/config.json

# Rotate logs if too large
logrotate -f config/logrotate.conf
```

### Common Log Patterns

#### Successful Operations
```
INFO - Request req-123 created successfully
INFO - Machine machine-456 provisioned successfully
INFO - Request req-123 completed with 3 machines
```

#### Error Patterns
```
ERROR - Failed to create request: Template template-1 not found
ERROR - AWS API error: InvalidParameterValue
ERROR - Database connection failed: timeout
```

#### Warning Patterns
```
WARN - Request req-123 taking longer than expected
WARN - AWS rate limit approaching
WARN - Database connection pool exhausted
```

### Log Analysis Commands
```bash
# Count error types
grep "ERROR" logs/app.log | cut -d':' -f3 | sort | uniq -c

# Find slow operations
grep "slow" logs/app.log | tail -20

# Monitor real-time errors
tail -f logs/app.log | grep "ERROR"

# Check request patterns
grep "Request.*created" logs/app.log | wc -l
```

## Performance Troubleshooting

### Database Performance
```bash
# Check database size
du -sh data/database.db

# Analyze slow queries
grep "slow query" logs/app.log

# Optimize database
python -m src.infrastructure.persistence.database.optimize
```

### Memory Usage
```bash
# Monitor memory usage
python -m src.infrastructure.diagnostics.memory_monitor

# Check for memory leaks
python -m memory_profiler src/bootstrap.py

# Reduce memory usage
# - Decrease connection pool size
# - Enable garbage collection
# - Use streaming for large datasets
```

### Network Issues
```bash
# Test AWS connectivity
curl -I https://ec2.amazonaws.com

# Check DNS resolution
nslookup ec2.amazonaws.com

# Test network latency
ping ec2.amazonaws.com
```

## Recovery Procedures

### Database Recovery
```bash
# Backup current state
cp -r data/ data_backup_$(date +%Y%m%d_%H%M%S)/

# Restore from backup
cp -r data_backup_20250630_100000/ data/

# Rebuild from events (if using event sourcing)
python -m src.infrastructure.events.rebuild_from_events
```

### Configuration Recovery
```bash
# Reset to default configuration
cp config/config.example.json config/config.json

# Restore from backup
cp config/config.json.backup config/config.json

# Validate restored configuration
python -m src.infrastructure.config.validate_config
```

### Provider Recovery
```bash
# Reset provider state
python -m src.providers.aws.reset_state

# Cleanup orphaned resources
python -m src.providers.aws.cleanup_orphaned_resources

# Re-register provider
python -m src.providers.aws.register
```

## Getting Help

### Documentation
- Check the [User Guide](../user_guide/) for usage instructions
- Review the [Developer Guide](../developer_guide/) for technical details
- Consult the [API Reference](api/) for API documentation

### Support Channels
1. **GitHub Issues**: Report bugs and request features
2. **Documentation**: Search the documentation for solutions
3. **Logs**: Check application logs for detailed error information
4. **Community**: Join discussions and ask questions

### Reporting Issues

When reporting issues, include:

1. **Environment Information**
   ```bash
   python --version
   pip list
   uname -a
   ```

2. **Configuration** (sanitized)
   ```bash
   # Remove sensitive information
   cat config/config.json | jq 'del(.aws.access_key, .aws.secret_key)'
   ```

3. **Error Logs**
   ```bash
   # Last 50 lines of logs
   tail -50 logs/app.log
   ```

4. **Steps to Reproduce**
   - Exact commands run
   - Expected behavior
   - Actual behavior

5. **System Information**
   ```bash
   python -m src.infrastructure.diagnostics.system_info
   ```

## Prevention

### Best Practices
- Regular backups of configuration and data
- Monitor system resources and logs
- Keep dependencies updated
- Use version control for configuration
- Test changes in development environment first

### Monitoring Setup
- Set up log rotation to prevent disk space issues
- Monitor application metrics and alerts
- Regular health checks of all components
- Automated backup procedures

### Maintenance
- Regular cleanup of old logs and data
- Update dependencies and security patches
- Review and optimize configuration periodically
- Test disaster recovery procedures

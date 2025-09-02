# Monitoring and Logging

This guide covers the logging, basic health checks, and monitoring capabilities of the Open Host Factory Plugin.

## Overview

The Open Host Factory Plugin provides basic monitoring capabilities through:

- **Application Logging**: Detailed operation logs
- **Health Checks**: Basic system health monitoring
- **Error Tracking**: Error detection and logging
- **Operation Tracking**: Request and machine lifecycle logging

## Logging

### Log Configuration

Configure logging in your `config.json`:

```json
{
  "logging": {
    "level": "INFO",
    "file_path": "logs/app.log",
    "console_enabled": true
  }
}
```

### Log Levels

- **DEBUG**: Detailed diagnostic information
- **INFO**: General operational information
- **WARNING**: Warning messages for potential issues
- **ERROR**: Error conditions
- **CRITICAL**: Critical errors that may cause failures

### Log Format

The application uses structured logging:

```
2025-06-30 10:00:00,123 INFO [RequestService] Request created successfully request_id=req-123 template_id=template-1 machine_count=3
2025-06-30 10:00:01,456 ERROR [AWSProvider] Failed to provision machine error=InvalidParameterValue request_id=req-123
```

### Log Analysis

#### Common Log Patterns

**Request Lifecycle:**
```bash
# Track request from creation to completion
grep "req-123" logs/app.log | grep -E "(created|status|completed)"
```

**Error Analysis:**
```bash
# Count error types
grep "ERROR" logs/app.log | cut -d']' -f2 | cut -d':' -f1 | sort | uniq -c

# Find recent errors
tail -100 logs/app.log | grep "ERROR"
```

**Performance Analysis:**
```bash
# Find slow operations
grep "slow" logs/app.log

# Track request duration
grep "Request.*completed" logs/app.log | grep -o "duration=[0-9]*"
```

### Log Rotation

For production environments, set up log rotation:

#### Using logrotate (Linux)
Create `/etc/logrotate.d/hostfactory`:

```
/path/to/logs/app.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 hostfactory hostfactory
}
```

#### Manual Log Management
```bash
# Archive old logs
mv logs/app.log logs/app.log.$(date +%Y%m%d)
touch logs/app.log

# Compress old logs
gzip logs/app.log.*

# Clean up old logs (keep last 30 days)
find logs/ -name "app.log.*" -mtime +30 -delete
```

## Health Checks

### Basic Health Check

The application provides basic health check functionality:

```bash
# Check if the application can start and load configuration
python run.py getAvailableTemplates

# Should return templates or empty list without errors
```

### AWS Connectivity Check

```bash
# Test AWS credentials and connectivity
aws sts get-caller-identity

# Test EC2 API access
aws ec2 describe-regions --region us-east-1
```

### Configuration Validation

```bash
# Validate configuration file
python -c "
import json
with open('config/config.json') as f:
    config = json.load(f)
    print('Configuration is valid JSON')
    print(f'Provider: {config.get(\"provider\", {}).get(\"type\", \"unknown\")}')
"
```

### Storage Health Check

```bash
# Check data directory
ls -la data/

# Check if database file is accessible
if [ -f "data/request_database.json" ]; then
    echo "Database file exists"
    python -c "
import json
with open('data/request_database.json') as f:
    data = json.load(f)
    print(f'Database loaded successfully')
"
else
    echo "Database file not found - will be created on first use"
fi
```

## Error Monitoring

### Error Types

The application logs various types of errors:

#### Configuration Errors
```
ERROR [ConfigManager] Failed to load configuration: File not found
ERROR [ConfigManager] Invalid JSON in configuration file
```

#### AWS Provider Errors
```
ERROR [AWSProvider] AWS API error: InvalidParameterValue
ERROR [AWSProvider] Failed to provision machine: InsufficientInstanceCapacity
ERROR [AWSProvider] Authentication failed: InvalidUserID.NotFound
```

#### Application Errors
```
ERROR [RequestService] Template not found: template-123
ERROR [RequestService] Invalid machine count: -1
ERROR [ApplicationService] Failed to create request: ValidationError
```

### Error Tracking Script

Create a simple error monitoring script:

```bash
#!/bin/bash
# error_monitor.sh

LOG_FILE="logs/app.log"
ERROR_COUNT=$(grep "ERROR" "$LOG_FILE" | wc -l)
RECENT_ERRORS=$(tail -100 "$LOG_FILE" | grep "ERROR" | wc -l)

echo "Total errors: $ERROR_COUNT"
echo "Recent errors (last 100 lines): $RECENT_ERRORS"

if [ "$RECENT_ERRORS" -gt 5 ]; then
    echo "WARNING: High error rate detected"
    echo "Recent errors:"
    tail -100 "$LOG_FILE" | grep "ERROR" | tail -5
fi
```

## Operation Monitoring

### Request Tracking

Monitor request lifecycle:

```bash
# Count active requests
python run.py getReturnRequests --active-only | jq '. | length'

# List recent requests
grep "Request.*created" logs/app.log | tail -10

# Track request completion
grep "Request.*completed" logs/app.log | tail -10
```

### Machine Monitoring

Track machine provisioning:

```bash
# Count machines by status
python run.py getReturnRequests | jq '.[] | .machines[] | .status' | sort | uniq -c

# Monitor provisioning time
grep "Machine.*provisioned" logs/app.log | tail -10
```

### AWS API Monitoring

Monitor AWS API usage:

```bash
# Count API calls
grep "AWS API" logs/app.log | wc -l

# Check for rate limiting
grep "rate limit" logs/app.log

# Monitor API errors
grep "AWS API.*error" logs/app.log | tail -10
```

## Performance Monitoring

### Response Time Tracking

Monitor command execution time:

```bash
# Time command execution
time python run.py getAvailableTemplates

# Monitor slow operations
grep "slow" logs/app.log
```

### Resource Usage

Monitor system resources:

```bash
# Check memory usage
ps aux | grep python | grep run.py

# Check disk usage
du -sh data/ logs/

# Monitor file handles
lsof | grep python | wc -l
```

### Database Performance

For JSON storage:

```bash
# Check database file size
ls -lh data/request_database.json

# Monitor database operations
grep "database" logs/app.log | tail -10
```

## Alerting

### Simple Email Alerts

Create a basic alerting script:

```bash
#!/bin/bash
# alert_check.sh

LOG_FILE="logs/app.log"
ALERT_EMAIL="admin@example.com"

# Check for critical errors
CRITICAL_ERRORS=$(grep "CRITICAL" "$LOG_FILE" | wc -l)

if [ "$CRITICAL_ERRORS" -gt 0 ]; then
    echo "CRITICAL errors detected in Host Factory Plugin" | \
    mail -s "Host Factory Alert" "$ALERT_EMAIL"
fi

# Check for high error rate
RECENT_ERRORS=$(tail -1000 "$LOG_FILE" | grep "ERROR" | wc -l)

if [ "$RECENT_ERRORS" -gt 50 ]; then
    echo "High error rate detected: $RECENT_ERRORS errors in last 1000 log lines" | \
    mail -s "Host Factory High Error Rate" "$ALERT_EMAIL"
fi
```

### Cron Job Setup

```bash
# Add to crontab
crontab -e

# Check every 15 minutes
*/15 * * * * /path/to/alert_check.sh

# Daily log summary
0 8 * * * /path/to/daily_summary.sh
```

## Monitoring Scripts

### Daily Summary Script

```bash
#!/bin/bash
# daily_summary.sh

LOG_FILE="logs/app.log"
DATE=$(date +%Y-%m-%d)

echo "Host Factory Daily Summary - $DATE"
echo "=================================="

# Request statistics
echo "Requests:"
echo "  Created: $(grep "Request.*created" "$LOG_FILE" | grep "$DATE" | wc -l)"
echo "  Completed: $(grep "Request.*completed" "$LOG_FILE" | grep "$DATE" | wc -l)"
echo "  Failed: $(grep "Request.*failed" "$LOG_FILE" | grep "$DATE" | wc -l)"

# Error statistics
echo "Errors:"
echo "  Total: $(grep "ERROR" "$LOG_FILE" | grep "$DATE" | wc -l)"
echo "  AWS: $(grep "ERROR.*AWS" "$LOG_FILE" | grep "$DATE" | wc -l)"
echo "  Config: $(grep "ERROR.*Config" "$LOG_FILE" | grep "$DATE" | wc -l)"

# Machine statistics
echo "Machines:"
echo "  Provisioned: $(grep "Machine.*provisioned" "$LOG_FILE" | grep "$DATE" | wc -l)"
echo "  Terminated: $(grep "Machine.*terminated" "$LOG_FILE" | grep "$DATE" | wc -l)"
```

### Health Check Script

```bash
#!/bin/bash
# health_check.sh

echo "Host Factory Health Check"
echo "========================"

# Test basic functionality
echo -n "Basic functionality: "
if python run.py getAvailableTemplates > /dev/null 2>&1; then
    echo "OK"
else
    echo "FAILED"
fi

# Test AWS connectivity
echo -n "AWS connectivity: "
if aws sts get-caller-identity > /dev/null 2>&1; then
    echo "OK"
else
    echo "FAILED"
fi

# Check disk space
echo -n "Disk space: "
DISK_USAGE=$(df . | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -lt 90 ]; then
    echo "OK ($DISK_USAGE%)"
else
    echo "WARNING ($DISK_USAGE%)"
fi

# Check log file size
echo -n "Log file size: "
if [ -f "logs/app.log" ]; then
    LOG_SIZE=$(du -m logs/app.log | cut -f1)
    if [ "$LOG_SIZE" -lt 100 ]; then
        echo "OK (${LOG_SIZE}MB)"
    else
        echo "WARNING (${LOG_SIZE}MB)"
    fi
else
    echo "No log file"
fi
```

## Log Analysis Tools

### Error Analysis

```bash
# Top error messages
grep "ERROR" logs/app.log | cut -d']' -f3 | sort | uniq -c | sort -nr | head -10

# Error timeline
grep "ERROR" logs/app.log | cut -d' ' -f1-2 | uniq -c

# AWS-specific errors
grep "ERROR.*AWS" logs/app.log | tail -20
```

### Performance Analysis

```bash
# Slow operations
grep -E "(slow|timeout|delay)" logs/app.log

# Request duration analysis
grep "duration=" logs/app.log | grep -o "duration=[0-9]*" | sort -n

# API call frequency
grep "AWS API" logs/app.log | cut -d' ' -f1-2 | uniq -c
```

## Troubleshooting Monitoring

### Common Issues

#### Log File Not Created
```bash
# Check directory permissions
ls -la logs/

# Create directory if needed
mkdir -p logs
chmod 755 logs
```

#### High Log File Size
```bash
# Check log file size
ls -lh logs/app.log

# Rotate logs manually
mv logs/app.log logs/app.log.old
touch logs/app.log
```

#### Missing Health Check Data
```bash
# Verify configuration
python -c "
import json
with open('config/config.json') as f:
    config = json.load(f)
    print('Logging config:', config.get('logging', {}))
"
```

## Integration with External Monitoring

### Syslog Integration

Configure syslog forwarding:

```python
# In logging configuration
{
  "logging": {
    "level": "INFO",
    "file_path": "logs/app.log",
    "console_enabled": true,
    "syslog_enabled": true,
    "syslog_facility": "local0"
  }
}
```

### Log Forwarding

Forward logs to centralized logging:

```bash
# Using rsyslog
echo "local0.*    @@logserver:514" >> /etc/rsyslog.conf
systemctl restart rsyslog

# Using filebeat (ELK stack)
# Configure filebeat.yml to monitor logs/app.log
```

## Next Steps

- **[Troubleshooting](troubleshooting.md)**: Learn how to diagnose and fix issues
- **[Configuration](configuration.md)**: Configure logging and monitoring settings
- **[Deployment](deployment.md)**: Deploy with monitoring in production
- **[API Reference](api_reference.md)**: Explore command-line interface

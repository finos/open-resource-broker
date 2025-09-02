# Operational Tools and Utilities

The Open Host Factory Plugin includes a comprehensive suite of operational tools for migration, backup, monitoring, and maintenance. These tools enable seamless operations and provide scalable operational capabilities.

## Overview

The operational toolset includes:

- **Repository Migration Tools**: Migrate data between storage strategies
- **Backup and Restore Utilities**: Comprehensive backup and recovery
- **Batch Processing Tools**: Handle large-scale operations efficiently
- **Utility Libraries**: Common operational functions
- **Resource Naming Tools**: Pattern-based naming and validation
- **Performance Monitoring**: Operational performance tracking

## Repository Migration Tools

### Migration Command

The built-in migration command enables seamless migration between storage strategies:

```bash
# Basic migration syntax
python run.py migrateRepository \
  --source-type <source> \
  --target-type <target> \
  --batch-size <size>

# Migration examples
python run.py migrateRepository --source-type json --target-type sqlite --batch-size 100
python run.py migrateRepository --source-type sqlite --target-type dynamodb --batch-size 50
python run.py migrateRepository --source-type json --target-type dynamodb --batch-size 25
```

### Migration Options

| Option | Description | Default | Example |
|--------|-------------|---------|---------|
| `--source-type` | Source storage type | Required | `json`, `sqlite`, `dynamodb` |
| `--target-type` | Target storage type | Required | `json`, `sqlite`, `dynamodb` |
| `--batch-size` | Records per batch | 100 | `50`, `100`, `200` |

**Note**: The migration command currently supports basic migration between storage types. Advanced options like backup creation, validation, and parallel processing are handled by the underlying migration system but are not exposed as command-line options.

### Migration Process

#### 1. Pre-Migration Validation

```bash
# The migration tool automatically validates:
# - Source connection and accessibility
# - Target connection and setup
# - Schema compatibility
# - Available disk space
# - Backup location accessibility
```

#### 2. Backup Creation

```bash
# Automatic backup creation
Creating backup: backups/migration_backup_20250630_120000.json
Backup size: 15.2 MB
Backup location: /path/to/backups/
```

#### 3. Batch Migration

```bash
# Migration progress output
Migrating templates: 100/100 (100%)
Migrating requests: 1,250/1,250 (100%)
Migrating machines: 3,750/3,750 (100%)

Migration Statistics:
- Total records: 5,100
- Migration time: 2m 15s
- Average speed: 37.8 records/second
- Errors: 0
```

#### 4. Post-Migration Validation

```bash
# Validation results
Validation Results:
[] Template count matches: 100
[] Request count matches: 1,250
[] Machine count matches: 3,750
[] Data integrity verified
[] Relationships validated
Migration completed successfully!
```

### Migration Configuration

The migration system uses the application's existing configuration and provides basic migration capabilities:

```json
{
  "storage": {
    "strategy": "json",
    "json_strategy": {
      "storage_type": "single_file",
      "base_path": "data"
    }
  }
}
```

**Migration Features**:
- **Basic Migration**: Move data between JSON, SQLite, and DynamoDB
- **Batch Processing**: Configurable batch sizes for performance
- **Progress Reporting**: Built-in progress tracking
- **Error Handling**: Automatic error detection and reporting

**Note**: Advanced features like parallel processing, custom backup creation, and detailed validation are handled internally by the migration system but are not exposed as configurable options in the current implementation.

## Backup and Restore Utilities

### Backup Operations

#### Manual Backup

```bash
# Create manual backup
python -c "
from src.infrastructure.persistence.repository_migrator import RepositoryMigrator
from src.infrastructure.di.container import get_container

container = get_container()
migrator = RepositoryMigrator(container)

backup_path = migrator.create_backup(
    source_type='json',
    backup_location='backups/manual_backup_$(date +%Y%m%d_%H%M%S).json'
)
print(f'Backup created: {backup_path}')
"
```

#### Automated Backup

```bash
# Set up automated backup (cron job)
# Add to crontab: crontab -e
0 2 * * * /path/to/backup_script.sh

# backup_script.sh
#!/bin/bash
cd /path/to/open-hostfactory-plugin
source .venv/bin/activate

BACKUP_DIR="backups/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# Backup configuration
cp config/config.json "$BACKUP_DIR/config_$(date +%H%M%S).json"

# Backup data
python -c "
from src.infrastructure.persistence.repository_migrator import RepositoryMigrator
from src.infrastructure.di.container import get_container

container = get_container()
migrator = RepositoryMigrator(container)
backup_path = migrator.create_backup('json', '$BACKUP_DIR/data_$(date +%H%M%S).json')
print(f'Data backup created: {backup_path}')
"

# Cleanup old backups (keep last 30 days)
find backups/ -type d -mtime +30 -exec rm -rf {} \;
```

### Restore Operations

#### Restore from Backup

```bash
# Restore data from backup
python -c "
from src.infrastructure.persistence.repository_migrator import RepositoryMigrator
from src.infrastructure.di.container import get_container

container = get_container()
migrator = RepositoryMigrator(container)

# Restore from specific backup
restore_result = migrator.restore_from_backup(
    backup_path='backups/migration_backup_20250630_120000.json',
    target_type='json'
)
print(f'Restore completed: {restore_result}')
"
```

#### Point-in-Time Recovery

```bash
# Restore to specific point in time
python -c "
from datetime import datetime
from src.infrastructure.persistence.repository_migrator import RepositoryMigrator

migrator = RepositoryMigrator(container)

# Find backup closest to target time
target_time = datetime(2025, 6, 30, 10, 0, 0)
backup_path = migrator.find_backup_by_time(target_time)

if backup_path:
    restore_result = migrator.restore_from_backup(backup_path, 'json')
    print(f'Point-in-time restore completed: {restore_result}')
else:
    print('No backup found for target time')
"
```

## Batch Processing Tools

### Batch Operation Framework

```python
from typing import List, Dict, Any, Callable, Iterator
from src.infrastructure.utilities.common.collections import batch_processor

class BatchProcessor:
    """Framework for batch processing operations."""

    def __init__(self, batch_size: int = 100, parallel_workers: int = 4):
        self.batch_size = batch_size
        self.parallel_workers = parallel_workers
        self.logger = get_logger(__name__)

    def process_in_batches(self, 
                          items: List[Any], 
                          processor: Callable[[List[Any]], None],
                          progress_callback: Callable[[int, int], None] = None) -> Dict[str, Any]:
        """Process items in batches."""
        total_items = len(items)
        processed_items = 0
        errors = []

        for batch in self._create_batches(items):
            try:
                processor(batch)
                processed_items += len(batch)

                if progress_callback:
                    progress_callback(processed_items, total_items)

            except Exception as e:
                error_info = {
                    'batch_start': processed_items,
                    'batch_size': len(batch),
                    'error': str(e)
                }
                errors.append(error_info)
                self.logger.error(f"Batch processing error: {e}")

        return {
            'total_items': total_items,
            'processed_items': processed_items,
            'errors': errors,
            'success_rate': processed_items / total_items if total_items > 0 else 0
        }

    def _create_batches(self, items: List[Any]) -> Iterator[List[Any]]:
        """Create batches from items."""
        for i in range(0, len(items), self.batch_size):
            yield items[i:i + self.batch_size]

# Usage examples
def batch_update_requests():
    """Example: Batch update request statuses."""
    from src.infrastructure.di.container import get_container

    container = get_container()
    request_repo = container.get(RequestRepositoryInterface)

    # Get all pending requests
    pending_requests = request_repo.query_entities(
        "requests", 
        filters={"status": "PENDING"}
    )

    def update_batch(batch):
        """Update a batch of requests."""
        for request_data in batch:
            # Update request status
            request_data['status'] = 'IN_PROGRESS'
            request_data['updated_at'] = datetime.utcnow().isoformat()
            request_repo.update_entity("requests", request_data['request_id'], request_data)

    def progress_callback(processed, total):
        """Progress reporting callback."""
        percentage = (processed / total) * 100
        print(f"Progress: {processed}/{total} ({percentage:.1f}%)")

    # Process in batches
    processor = BatchProcessor(batch_size=50)
    result = processor.process_in_batches(
        pending_requests, 
        update_batch, 
        progress_callback
    )

    print(f"Batch update completed: {result}")
```

### Batch Operations Examples

#### Batch Template Validation

```bash
# Validate all templates in batch
python -c "
from src.infrastructure.utilities.common.collections.validation import batch_validate_templates

# Get all templates
templates = get_all_templates()

# Validate in batches
validation_results = batch_validate_templates(
    templates, 
    batch_size=25,
    parallel=True
)

print(f'Validation completed: {validation_results}')
"
```

#### Batch Machine Status Update

```bash
# Update machine statuses in batch
python -c "
from src.infrastructure.utilities.common.collections.transforming import batch_transform

# Get all machines
machines = get_all_machines()

# Update statuses in batch
def update_machine_status(machine_batch):
    for machine in machine_batch:
        # Get current status from provider
        current_status = provider.get_machine_status([machine['machine_id']])
        machine['status'] = current_status[0]['status']
        machine['updated_at'] = datetime.utcnow().isoformat()

batch_transform(machines, update_machine_status, batch_size=20)
print('Machine status update completed')
"
```

## Utility Libraries

### File Utilities

The application includes comprehensive file utilities:

```python
from src.infrastructure.utilities.common.file_utils import (
    atomic_write,
    safe_read,
    backup_file,
    compress_file,
    validate_json_file
)

# Atomic file operations
atomic_write('data/important.json', json_data)

# Safe file reading with error handling
data = safe_read('config/config.json', default={})

# File backup with timestamp
backup_path = backup_file('data/database.json')

# File compression
compressed_path = compress_file('logs/app.log')

# JSON validation
is_valid, errors = validate_json_file('config/templates.json')
```

### Collection Utilities

Advanced collection processing utilities:

```python
from src.infrastructure.utilities.common.collections import (
    grouping,
    filtering,
    transforming,
    validation
)

# Group requests by status
requests_by_status = grouping.group_by_field(requests, 'status')

# Filter active machines
active_machines = filtering.filter_by_criteria(
    machines, 
    lambda m: m['status'] in ['RUNNING', 'PENDING']
)

# Transform machine data
transformed_machines = transforming.transform_collection(
    machines,
    lambda m: {
        'id': m['machine_id'],
        'status': m['status'].lower(),
        'ip': m.get('private_ip', 'unknown')
    }
)

# Validate collection consistency
validation_errors = validation.validate_collection_consistency(
    requests, 
    machines,
    relationship_field='request_id'
)
```

### Date Utilities

Comprehensive date and time utilities:

```python
from src.infrastructure.utilities.common.date_utils import (
    format_timestamp,
    parse_iso_date,
    calculate_duration,
    get_time_ranges
)

# Format timestamps consistently
formatted_time = format_timestamp(datetime.utcnow(), 'iso')

# Parse various date formats
parsed_date = parse_iso_date('2025-06-30T12:00:00Z')

# Calculate operation duration
duration = calculate_duration(start_time, end_time)

# Get time ranges for reporting
today_range = get_time_ranges('today')
week_range = get_time_ranges('week')
```

### String Utilities

String processing and validation utilities:

```python
from src.infrastructure.utilities.common.string_utils import (
    sanitize_filename,
    validate_pattern,
    template_substitute,
    generate_id
)

# Sanitize filenames for safe storage
safe_filename = sanitize_filename(user_input)

# Validate against patterns
is_valid_id = validate_pattern(resource_id, r'^[a-zA-Z0-9_-]+$')

# Template substitution
result = template_substitute(
    'Request {request_id} has {machine_count} machines',
    {'request_id': 'req-123', 'machine_count': 3}
)

# Generate unique IDs
unique_id = generate_id('req')  # Returns: req-12345678-1234-1234-1234-123456789012
```

## Resource Naming Tools

### Naming Pattern Validation

```python
from src.infrastructure.utilities.common.resource_naming import (
    validate_resource_name,
    generate_resource_name,
    parse_resource_name
)

# Validate resource names against patterns
is_valid = validate_resource_name('template-web-server', 'template')
is_valid_ec2 = validate_resource_name('i-1234567890abcdef0', 'ec2_instance')

# Generate compliant resource names
template_name = generate_resource_name('template', 'web-server')
request_name = generate_resource_name('request', prefix='prod')

# Parse resource names
parsed = parse_resource_name('req-12345678-1234-1234-1234-123456789012')
# Returns: {'type': 'request', 'id': '12345678-1234-1234-1234-123456789012'}
```

### Naming Configuration

Resource naming is fully configurable:

```json
{
  "naming": {
    "patterns": {
      "template_id": "^template-[a-zA-Z0-9_-]+$",
      "request_id": "^(req|ret)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
      "machine_id": "^machine-[a-zA-Z0-9_-]+$"
    },
    "prefixes": {
      "request": "req-",
      "return_request": "ret-",
      "machine": "machine-",
      "template": "template-"
    },
    "generators": {
      "request_id": "uuid4_with_prefix",
      "machine_id": "sequential_with_prefix",
      "template_id": "name_with_prefix"
    }
  }
}
```

## Performance Monitoring Tools

### Operational Metrics Collection

```python
from src.infrastructure.utilities.performance import (
    PerformanceMonitor,
    OperationTimer,
    ResourceMonitor
)

# Monitor operation performance
monitor = PerformanceMonitor()

with OperationTimer('request_creation') as timer:
    # Perform operation
    result = create_request(template_id, machine_count)
    timer.add_metadata({'template_id': template_id, 'machine_count': machine_count})

# Get performance metrics
metrics = monitor.get_metrics()
print(f"Average request creation time: {metrics['request_creation']['avg_time']:.2f}s")

# Monitor resource usage
resource_monitor = ResourceMonitor()
resource_stats = resource_monitor.get_current_stats()
print(f"Memory usage: {resource_stats['memory_percent']:.1f}%")
print(f"CPU usage: {resource_stats['cpu_percent']:.1f}%")
```

### Performance Analysis Tools

```bash
# Analyze operation performance
python -c "
from src.infrastructure.utilities.performance import analyze_performance_logs

# Analyze recent performance
analysis = analyze_performance_logs('logs/app.log', hours=24)

print('Performance Analysis (Last 24 Hours):')
print(f'Total operations: {analysis[\"total_operations\"]}')
print(f'Average response time: {analysis[\"avg_response_time\"]:.2f}s')
print(f'95th percentile: {analysis[\"p95_response_time\"]:.2f}s')
print(f'Error rate: {analysis[\"error_rate\"]:.2f}%')

# Slowest operations
print('\\nSlowest Operations:')
for op in analysis['slowest_operations'][:5]:
    print(f'  {op[\"operation\"]}: {op[\"time\"]:.2f}s')
"
```

## Maintenance Tools

### Database Maintenance

```bash
# Optimize database performance
python -c "
from src.infrastructure.persistence.maintenance import DatabaseMaintenance

maintenance = DatabaseMaintenance()

# Analyze database
analysis = maintenance.analyze_database()
print(f'Database size: {analysis[\"size_mb\"]} MB')
print(f'Record count: {analysis[\"record_count\"]}')
print(f'Fragmentation: {analysis[\"fragmentation_percent\"]}%')

# Optimize if needed
if analysis['fragmentation_percent'] > 20:
    maintenance.optimize_database()
    print('Database optimization completed')
"
```

### Log Management

```bash
# Log rotation and cleanup
python -c "
from src.infrastructure.utilities.log_management import LogManager

log_manager = LogManager()

# Rotate logs
log_manager.rotate_logs('logs/app.log', max_size_mb=100)

# Cleanup old logs
cleaned_files = log_manager.cleanup_old_logs('logs/', days=30)
print(f'Cleaned up {len(cleaned_files)} old log files')

# Compress logs
compressed_files = log_manager.compress_logs('logs/', pattern='*.log.*')
print(f'Compressed {len(compressed_files)} log files')
"
```

### System Health Checks

```bash
# Comprehensive system health check
python -c "
from src.infrastructure.utilities.health_check import SystemHealthCheck

health_checker = SystemHealthCheck()

# Run all health checks
health_report = health_checker.run_all_checks()

print('System Health Report:')
for component, status in health_report.items():
    status_icon = '[]' if status['healthy'] else '[]'
    print(f'  {status_icon} {component}: {status[\"message\"]}')

# Overall health score
overall_score = health_checker.calculate_health_score(health_report)
print(f'\\nOverall Health Score: {overall_score}/100')
"
```

## Automation Scripts

### Operational Automation

Create automation scripts for common operations:

```bash
#!/bin/bash
# operational_maintenance.sh

set -e

echo "Starting operational maintenance..."

# 1. Health check
echo "Running health checks..."
python -c "
from src.infrastructure.utilities.health_check import SystemHealthCheck
health_checker = SystemHealthCheck()
health_report = health_checker.run_all_checks()
if not all(status['healthy'] for status in health_report.values()):
    print('Health check failed!')
    exit(1)
print('Health check passed')
"

# 2. Database maintenance
echo "Running database maintenance..."
python -c "
from src.infrastructure.persistence.maintenance import DatabaseMaintenance
maintenance = DatabaseMaintenance()
maintenance.optimize_database()
print('Database maintenance completed')
"

# 3. Log cleanup
echo "Cleaning up logs..."
find logs/ -name "*.log.*" -mtime +7 -exec gzip {} \;
find logs/ -name "*.gz" -mtime +30 -delete
echo "Log cleanup completed"

# 4. Backup creation
echo "Creating backup..."
python -c "
from src.infrastructure.persistence.repository_migrator import RepositoryMigrator
from src.infrastructure.di.container import get_container
container = get_container()
migrator = RepositoryMigrator(container)
backup_path = migrator.create_backup('json', 'backups/maintenance_backup_$(date +%Y%m%d_%H%M%S).json')
print(f'Backup created: {backup_path}')
"

echo "Operational maintenance completed successfully!"
```

## Best Practices

### Operational Guidelines

1. **Regular Backups**: Implement automated backup procedures
2. **Performance Monitoring**: Monitor operational performance continuously
3. **Batch Processing**: Use batch operations for large-scale changes
4. **Resource Cleanup**: Regularly clean up old data and logs
5. **Health Monitoring**: Implement comprehensive health checks

### Migration Best Practices

1. **Test Migrations**: Always test migrations in non-production environments
2. **Backup First**: Create backups before any migration
3. **Validate Results**: Verify data integrity after migration
4. **Monitor Performance**: Monitor migration performance and adjust batch sizes
5. **Plan Rollback**: Have rollback procedures ready

### Maintenance Scheduling

```bash
# Example cron schedule for operational tasks

# Daily backup at 2 AM
0 2 * * * /path/to/backup_script.sh

# Weekly database optimization on Sunday at 3 AM
0 3 * * 0 /path/to/database_maintenance.sh

# Monthly log cleanup on first day at 4 AM
0 4 1 * * /path/to/log_cleanup.sh

# Hourly health checks
0 * * * * /path/to/health_check.sh
```

## Troubleshooting

### Common Issues

#### Migration Failures
```bash
# Check migration logs
tail -f logs/app.log | grep migration

# Validate source data
python -c "
from src.infrastructure.persistence.repository_migrator import RepositoryMigrator
migrator = RepositoryMigrator(container)
validation_result = migrator.validate_source_data('json')
print(f'Source validation: {validation_result}')
"
```

#### Performance Issues
```bash
# Monitor resource usage during operations
top -p $(pgrep -f "python.*run.py")

# Check batch processing performance
python -c "
from src.infrastructure.utilities.performance import analyze_batch_performance
analysis = analyze_batch_performance()
print(f'Batch performance: {analysis}')
"
```

#### Storage Issues
```bash
# Check disk space
df -h data/ logs/ backups/

# Check file permissions
ls -la data/ logs/ backups/

# Validate data integrity
python -c "
from src.infrastructure.utilities.data_validation import validate_data_integrity
result = validate_data_integrity()
print(f'Data integrity: {result}')
"
```

## Next Steps

- **[Performance Tuning](performance.md)**: Optimize system performance
- **[Backup and Recovery](backup_recovery.md)**: Comprehensive backup procedures
- **[Monitoring Setup](monitoring.md)**: Set up monitoring and alerting
- **[Troubleshooting](../user_guide/troubleshooting.md)**: Diagnose and fix issues

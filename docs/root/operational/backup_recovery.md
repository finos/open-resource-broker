# Backup and Recovery

This guide covers comprehensive backup and recovery procedures for the Open Host Factory Plugin, including data protection strategies, automated backup procedures, and disaster recovery planning.

## Backup Overview

The application supports multiple backup strategies depending on your storage configuration:
- **JSON Storage**: File-based backups with versioning
- **SQL Storage**: Database dumps and point-in-time recovery
- **DynamoDB Storage**: AWS native backup and restore
- **Configuration Backups**: Application and template configuration
- **Operational Backups**: Logs, metrics, and operational data

## Backup Strategies

### JSON Storage Backup

#### Automatic Backup Configuration
```json
{
  "storage": {
    "strategy": "json",
    "json_strategy": {
      "backup_on_write": true,
      "max_backup_files": 10,
      "backup_compression": true,
      "backup_location": "backups/json/"
    }
  }
}
```

#### Manual JSON Backup
```bash
# Create manual backup
python -c "
from src.infrastructure.persistence.repository_migrator import RepositoryMigrator
from src.infrastructure.di.container import get_container

container = get_container()
migrator = RepositoryMigrator(container)

backup_path = migrator.create_backup(
    source_type='json',
    backup_location=f'backups/manual_backup_{datetime.now().strftime(\"%Y%m%d_%H%M%S\")}.json'
)
print(f'Backup created: {backup_path}')
"

# Verify backup integrity
python -c "
import json
with open('backups/manual_backup_20250630_120000.json') as f:
    data = json.load(f)
    print(f'Templates: {len(data.get(\"templates\", {}))}')
    print(f'Requests: {len(data.get(\"requests\", {}))}')
    print(f'Machines: {len(data.get(\"machines\", {}))}')
"
```

#### Automated JSON Backup Script
```bash
#!/bin/bash
# json_backup.sh

BACKUP_DIR="backups/json"
RETENTION_DAYS=30
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Create backup
python -c "
from src.infrastructure.persistence.repository_migrator import RepositoryMigrator
from src.infrastructure.di.container import get_container
import os

container = get_container()
migrator = RepositoryMigrator(container)

backup_path = migrator.create_backup(
    source_type='json',
    backup_location='$BACKUP_DIR/backup_$DATE.json'
)
print(f'Backup created: {backup_path}')
"

# Compress backup
gzip "$BACKUP_DIR/backup_$DATE.json"

# Clean up old backups
find "$BACKUP_DIR" -name "backup_*.json.gz" -mtime +$RETENTION_DAYS -delete

echo "JSON backup completed: backup_$DATE.json.gz"
```

### SQL Storage Backup

The application supports multiple SQL databases. Choose the appropriate backup method for your database type:

#### SQLite Backup (Primary SQL Option)
```bash
#!/bin/bash
# sqlite_backup.sh

DB_FILE="request_database.db"
BACKUP_DIR="backups/sqlite"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=30

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Create backup using SQLite backup API
sqlite3 "$DB_FILE" ".backup $BACKUP_DIR/backup_$DATE.db"

# Create SQL dump
sqlite3 "$DB_FILE" ".dump" > "$BACKUP_DIR/dump_$DATE.sql"

# Compress backups
gzip "$BACKUP_DIR/backup_$DATE.db"
gzip "$BACKUP_DIR/dump_$DATE.sql"

# Clean up old backups
find "$BACKUP_DIR" -name "backup_*.db.gz" -mtime +$RETENTION_DAYS -delete
find "$BACKUP_DIR" -name "dump_*.sql.gz" -mtime +$RETENTION_DAYS -delete

echo "SQLite backup completed: backup_$DATE.db.gz"
```

#### PostgreSQL Backup (Enterprise Option)

**Note**: PostgreSQL support is available for enterprise deployments. Most installations use SQLite or DynamoDB.

```bash
#!/bin/bash
# postgresql_backup.sh - For enterprise PostgreSQL deployments

DB_NAME="hostfactory"
DB_USER="hostfactory_user"
BACKUP_DIR="backups/postgresql"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Full database backup
pg_dump -h localhost -U "$DB_USER" -d "$DB_NAME" \
  --format=custom \
  --compress=9 \
  --file="$BACKUP_DIR/full_backup_$DATE.dump"

echo "PostgreSQL backup completed: full_backup_$DATE.dump"
```
```bash
#!/bin/bash
# sqlite_backup.sh

DB_FILE="request_database.db"
BACKUP_DIR="backups/sqlite"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=30

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Create backup using SQLite backup API
sqlite3 "$DB_FILE" ".backup $BACKUP_DIR/backup_$DATE.db"

# Create SQL dump
sqlite3 "$DB_FILE" ".dump" > "$BACKUP_DIR/dump_$DATE.sql"

# Compress backups
gzip "$BACKUP_DIR/backup_$DATE.db"
gzip "$BACKUP_DIR/dump_$DATE.sql"

# Clean up old backups
find "$BACKUP_DIR" -name "backup_*.db.gz" -mtime +$RETENTION_DAYS -delete
find "$BACKUP_DIR" -name "dump_*.sql.gz" -mtime +$RETENTION_DAYS -delete

echo "SQLite backup completed: backup_$DATE.db.gz"
```

### DynamoDB Backup

#### On-Demand Backup
```bash
#!/bin/bash
# dynamodb_backup.sh

TABLE_PREFIX="hostfactory"
BACKUP_PREFIX="backup"
DATE=$(date +%Y%m%d_%H%M%S)

# Backup all tables
for table in "${TABLE_PREFIX}_templates" "${TABLE_PREFIX}_requests" "${TABLE_PREFIX}_machines"; do
    aws dynamodb create-backup \
        --table-name "$table" \
        --backup-name "${BACKUP_PREFIX}_${table}_$DATE"

    echo "Created backup for table: $table"
done

# List recent backups
aws dynamodb list-backups \
    --table-name "${TABLE_PREFIX}_requests" \
    --time-range-lower-bound $(date -d '7 days ago' +%s) \
    --query 'BackupSummaries[*].[BackupName,BackupCreationDateTime,BackupStatus]' \
    --output table
```

#### Point-in-Time Recovery Setup
```bash
# Enable point-in-time recovery
aws dynamodb update-continuous-backups \
    --table-name hostfactory_requests \
    --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true

aws dynamodb update-continuous-backups \
    --table-name hostfactory_machines \
    --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true

aws dynamodb update-continuous-backups \
    --table-name hostfactory_templates \
    --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true
```

## Configuration Backup

### Application Configuration Backup
```bash
#!/bin/bash
# config_backup.sh

CONFIG_DIR="config"
BACKUP_DIR="backups/config"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Backup configuration files
tar -czf "$BACKUP_DIR/config_backup_$DATE.tar.gz" "$CONFIG_DIR"

# Backup environment-specific configurations
if [ -f ".env" ]; then
    cp ".env" "$BACKUP_DIR/env_backup_$DATE"
fi

# Backup templates
if [ -f "$CONFIG_DIR/templates.json" ]; then
    cp "$CONFIG_DIR/templates.json" "$BACKUP_DIR/templates_backup_$DATE.json"
fi

echo "Configuration backup completed: config_backup_$DATE.tar.gz"
```

### Template Backup from SSM
```bash
#!/bin/bash
# ssm_template_backup.sh

SSM_PREFIX="/hostfactory/templates/"
BACKUP_DIR="backups/templates"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Get all template parameters
aws ssm get-parameters-by-path \
    --path "$SSM_PREFIX" \
    --recursive \
    --query 'Parameters[*].[Name,Value]' \
    --output json > "$BACKUP_DIR/ssm_templates_$DATE.json"

echo "SSM template backup completed: ssm_templates_$DATE.json"
```

## Recovery Procedures

### JSON Storage Recovery

#### Full Recovery from Backup
```bash
# Restore from backup
python -c "
from src.infrastructure.persistence.repository_migrator import RepositoryMigrator
from src.infrastructure.di.container import get_container

container = get_container()
migrator = RepositoryMigrator(container)

# Restore from specific backup
restore_result = migrator.restore_from_backup(
    backup_path='backups/json/backup_20250630_120000.json.gz',
    target_type='json'
)
print(f'Restore completed: {restore_result}')
"
```

#### Selective Recovery
```python
# Restore specific data types
import json
import gzip

def restore_templates_only(backup_path, target_path):
    """Restore only templates from backup."""

    # Load backup data
    with gzip.open(backup_path, 'rt') as f:
        backup_data = json.load(f)

    # Load current data
    with open(target_path, 'r') as f:
        current_data = json.load(f)

    # Restore only templates
    current_data['templates'] = backup_data.get('templates', {})

    # Save updated data
    with open(target_path, 'w') as f:
        json.dump(current_data, f, indent=2)

    print(f"Restored {len(current_data['templates'])} templates")

# Usage
restore_templates_only(
    'backups/json/backup_20250630_120000.json.gz',
    'data/request_database.json'
)
```

### SQL Storage Recovery

#### PostgreSQL Recovery
```bash
# Full database restore
pg_restore -h localhost -U hostfactory_user -d hostfactory_restored \
    --clean --create \
    backups/postgresql/full_backup_20250630_120000.dump

# Selective table restore
pg_restore -h localhost -U hostfactory_user -d hostfactory \
    --table=requests \
    --data-only \
    backups/postgresql/data_backup_20250630_120000.dump
```

#### Point-in-Time Recovery
```bash
# PostgreSQL point-in-time recovery
# 1. Stop the database
sudo systemctl stop postgresql

# 2. Restore base backup
tar -xzf backups/postgresql/base_backup_20250630.tar.gz -C /var/lib/postgresql/data/

# 3. Create recovery configuration
cat > /var/lib/postgresql/data/recovery.conf << EOF
restore_command = 'cp /var/lib/postgresql/wal_archive/%f %p'
recovery_target_time = '2025-06-30 12:30:00'
EOF

# 4. Start database
sudo systemctl start postgresql
```

#### SQLite Recovery
```bash
# Restore SQLite database
gunzip -c backups/sqlite/backup_20250630_120000.db.gz > request_database.db

# Verify restored database
sqlite3 request_database.db "
SELECT 
    'templates' as table_name, COUNT(*) as count FROM templates
UNION ALL
SELECT 
    'requests' as table_name, COUNT(*) as count FROM requests
UNION ALL
SELECT 
    'machines' as table_name, COUNT(*) as count FROM machines;
"
```

### DynamoDB Recovery

#### Restore from Backup
```bash
# Restore table from backup
aws dynamodb restore-table-from-backup \
    --target-table-name hostfactory_requests_restored \
    --backup-arn arn:aws:dynamodb:us-east-1:123456789012:table/hostfactory_requests/backup/01234567890123-abcdefgh

# Point-in-time recovery
aws dynamodb restore-table-to-point-in-time \
    --source-table-name hostfactory_requests \
    --target-table-name hostfactory_requests_restored \
    --restore-date-time 2025-06-30T12:30:00Z
```

## Disaster Recovery Planning

### Recovery Time Objectives (RTO)

| Component | RTO Target | Recovery Method |
|-----------|------------|-----------------|
| JSON Storage | 15 minutes | File restore from backup |
| SQLite Storage | 30 minutes | Database file restore |
| PostgreSQL Storage | 1 hour | Database restore from dump |
| DynamoDB Storage | 2 hours | AWS backup restore |
| Configuration | 5 minutes | File restore |
| Application | 10 minutes | Redeploy from source |

### Recovery Point Objectives (RPO)

| Component | RPO Target | Backup Frequency |
|-----------|------------|------------------|
| JSON Storage | 1 hour | Hourly automated backup |
| SQL Storage | 4 hours | Every 4 hours |
| DynamoDB Storage | 24 hours | Daily backup + PITR |
| Configuration | 24 hours | Daily backup |
| Logs | 1 hour | Real-time log shipping |

### Disaster Recovery Procedures

#### Complete System Recovery
```bash
#!/bin/bash
# disaster_recovery.sh

RECOVERY_DATE="20250630_120000"
BACKUP_BASE="backups"

echo "Starting disaster recovery for $RECOVERY_DATE"

# 1. Restore configuration
echo "Restoring configuration..."
tar -xzf "$BACKUP_BASE/config/config_backup_$RECOVERY_DATE.tar.gz"

# 2. Restore data based on storage strategy
STORAGE_STRATEGY=$(python -c "
import json
with open('config/config.json') as f:
    config = json.load(f)
    print(config['storage']['strategy'])
")

case $STORAGE_STRATEGY in
    "json")
        echo "Restoring JSON storage..."
        gunzip -c "$BACKUP_BASE/json/backup_$RECOVERY_DATE.json.gz" > data/request_database.json
        ;;
    "sql")
        echo "Restoring SQL storage..."
        # Restore based on SQL type
        ;;
    "dynamodb")
        echo "Restoring DynamoDB storage..."
        # Restore DynamoDB tables
        ;;
esac

# 3. Restore templates
echo "Restoring templates..."
cp "$BACKUP_BASE/templates/templates_backup_$RECOVERY_DATE.json" config/templates.json

# 4. Verify recovery
echo "Verifying recovery..."
python run.py getAvailableTemplates > /dev/null
if [ $? -eq 0 ]; then
    echo "[] Recovery successful - application is functional"
else
    echo "[] Recovery failed - application not responding"
    exit 1
fi

echo "Disaster recovery completed successfully"
```

## Backup Automation

### Cron-based Backup Schedule
```bash
# Add to crontab: crontab -e

# Hourly JSON backup
0 * * * * /opt/hostfactory/scripts/json_backup.sh >> /var/log/hostfactory/backup.log 2>&1

# Daily SQL backup
0 2 * * * /opt/hostfactory/scripts/postgresql_backup.sh >> /var/log/hostfactory/backup.log 2>&1

# Daily configuration backup
0 3 * * * /opt/hostfactory/scripts/config_backup.sh >> /var/log/hostfactory/backup.log 2>&1

# Weekly DynamoDB backup
0 4 * * 0 /opt/hostfactory/scripts/dynamodb_backup.sh >> /var/log/hostfactory/backup.log 2>&1

# Monthly backup verification
0 5 1 * * /opt/hostfactory/scripts/backup_verification.sh >> /var/log/hostfactory/backup.log 2>&1
```

### Backup Monitoring
```python
#!/usr/bin/env python3
# backup_monitor.py

import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

class BackupMonitor:
    def __init__(self, config_path="config/backup_config.json"):
        with open(config_path) as f:
            self.config = json.load(f)

    def check_backup_freshness(self):
        """Check if backups are recent enough."""
        issues = []

        for backup_type, config in self.config['backup_types'].items():
            backup_dir = Path(config['directory'])
            max_age_hours = config['max_age_hours']

            if not backup_dir.exists():
                issues.append(f"Backup directory missing: {backup_dir}")
                continue

            # Find most recent backup
            backup_files = list(backup_dir.glob(config['pattern']))
            if not backup_files:
                issues.append(f"No backup files found in {backup_dir}")
                continue

            latest_backup = max(backup_files, key=os.path.getmtime)
            backup_age = time.time() - os.path.getmtime(latest_backup)
            backup_age_hours = backup_age / 3600

            if backup_age_hours > max_age_hours:
                issues.append(
                    f"Backup too old: {latest_backup} "
                    f"({backup_age_hours:.1f}h > {max_age_hours}h)"
                )

        return issues

    def verify_backup_integrity(self):
        """Verify backup file integrity."""
        issues = []

        # Check JSON backups
        json_backups = Path("backups/json").glob("*.json*")
        for backup_file in json_backups:
            try:
                if backup_file.suffix == '.gz':
                    import gzip
                    with gzip.open(backup_file, 'rt') as f:
                        json.load(f)
                else:
                    with open(backup_file) as f:
                        json.load(f)
            except Exception as e:
                issues.append(f"Corrupt backup: {backup_file} - {e}")

        return issues

if __name__ == "__main__":
    monitor = BackupMonitor()

    freshness_issues = monitor.check_backup_freshness()
    integrity_issues = monitor.verify_backup_integrity()

    all_issues = freshness_issues + integrity_issues

    if all_issues:
        print("Backup issues detected:")
        for issue in all_issues:
            print(f"  - {issue}")
        exit(1)
    else:
        print("All backups are healthy")
        exit(0)
```

## Best Practices

### Backup Strategy Guidelines

1. **Follow the 3-2-1 Rule**
   - 3 copies of important data
   - 2 different storage media
   - 1 offsite backup

2. **Test Recovery Procedures**
   - Regular recovery testing
   - Document recovery procedures
   - Train team on recovery processes

3. **Monitor Backup Health**
   - Automated backup verification
   - Alert on backup failures
   - Regular backup integrity checks

4. **Secure Backup Storage**
   - Encrypt backup files
   - Secure backup locations
   - Control access to backups

5. **Optimize Backup Performance**
   - Schedule backups during low-usage periods
   - Use compression to reduce storage
   - Implement incremental backups where possible

### Recovery Planning

1. **Document Recovery Procedures**
   - Step-by-step recovery instructions
   - Contact information for key personnel
   - Dependencies and prerequisites

2. **Establish Recovery Priorities**
   - Critical vs non-critical data
   - Recovery time objectives
   - Recovery point objectives

3. **Regular Testing**
   - Monthly recovery drills
   - Annual disaster recovery exercises
   - Update procedures based on test results

## Next Steps

- **[Performance Tuning](performance.md)**: Optimize backup and recovery performance
- **[Monitoring](../user_guide/monitoring.md)**: Set up backup monitoring and alerting
- **[Configuration](../configuration-guide.md)**: Configure backup settings
- **[Troubleshooting](../user_guide/troubleshooting.md)**: Diagnose backup and recovery issues

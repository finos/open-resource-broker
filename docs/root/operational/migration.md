# Migration Procedures

This document provides detailed procedures for migrating between different storage strategies and configurations.

## Overview

The Open Resource Broker supports migration between different storage backends and configuration formats. This guide covers:

- Storage strategy migration
- Configuration format migration
- Data backup and recovery
- Rollback procedures

## Storage Strategy Migration

### Supported Migrations

The plugin supports migration between:

- JSON file storage <-> SQLite database
- Single file <-> Multi-file storage
- Local storage <-> Network storage

### Migration Command

Use the built-in migration command:

```bash
# Migrate from JSON to SQLite
orb storage migrate --source json --target sqlite

# Migrate with backup
orb storage migrate --source json --target sqlite --backup
```

### Pre-Migration Checklist

Before starting migration:

1. **Backup existing data**
   ```bash
   # Create backup
   cp -r data/ data-backup-$(date +%Y%m%d)
   ```

2. **Stop active operations**
   - Ensure no active machine requests
   - Stop any scheduled operations

3. **Verify source data integrity**
   ```bash
   orb storage test
   ```

### Migration Steps

1. **Prepare target storage**
   ```bash
   # Initialize target storage
   orb storage init --type sqlite
   ```

2. **Run migration**
   ```bash
   # Execute migration
   orb storage migrate --source json --target sqlite --verify
   ```

3. **Verify migration**
   ```bash
   # Test target storage
   orb storage test --type sqlite

   # Compare data counts
   orb requests list --count
   ```

4. **Update configuration**
   ```bash
   # Update config to use new storage
   orb config update --storage-type sqlite
   ```

### Post-Migration Verification

After migration:

1. **Test basic operations**
   ```bash
   orb templates list
   orb requests list
   ```

2. **Verify data integrity**
   ```bash
   orb storage validate
   ```

3. **Test provider operations**
   ```bash
   orb providers health
   ```

## SQL Schema Upgrades

SQL storage (SQLite, PostgreSQL, MySQL and Aurora MySQL) keeps its schema under Alembic. After upgrading ORB, apply the pending schema migrations:

```bash
orb storage migrate up
```

When a database is behind the migrations the installed release expects, ORB logs a warning at startup naming the database's revision and the expected one, and tells you to run this command. It never upgrades a versioned database by itself, so you choose when the change runs. Back up the database first.

Release notes for schema changes:

- **`machines.launch_time` is now TEXT.** It was declared as a BIGINT while ORB stores an ISO-8601 timestamp, so PostgreSQL and MySQL rejected machines that had a launch time. The migration converts the column (existing epoch values, in seconds or milliseconds, are rewritten as ISO-8601 UTC text, so sorting by launch time stays chronological; a downgrade converts them back and drops fractions of a second). A launch time that is neither an epoch nor ISO-8601 text stops the upgrade or downgrade before anything is changed, naming the number of rows and the first machine id. On PostgreSQL and MySQL, run `orb storage migrate up` before relying on machines with launch times; until then the column keeps rejecting them.
- A database that has tables but no Alembic version (created before schema versioning) is recognised when ORB starts: it is recorded at the revision that matches its layout and upgraded to the current one automatically, so no manual step is needed for it.

## Configuration Migration

### Legacy Configuration Support

The plugin maintains backward compatibility with legacy configuration formats while supporting new formats.

### Migration Tools

Use configuration migration tools:

```bash
# Convert legacy config
orb config migrate --from legacy --to current

# Validate migrated config
orb config validate
```

## Rollback Procedures

If migration fails or issues arise:

1. **Stop the application**
2. **Restore from backup**
   ```bash
   rm -rf data/
   cp -r data-backup-YYYYMMDD/ data/
   ```
3. **Revert configuration changes**
4. **Restart with original settings**

## Troubleshooting

Common migration issues:

- **Data corruption**: Use backup and retry
- **Permission errors**: Check file permissions
- **Storage conflicts**: Ensure target storage is empty
- **Configuration errors**: Validate configuration syntax

## Best Practices

- Always create backups before migration
- Test migration in non-production environment first
- Monitor system during migration
- Have rollback plan ready
- Document migration steps and results

## Related Documentation

- [Storage Strategies](../user_guide/storage_strategies.md)
- [Configuration Guide](../user_guide/configuration.md)
- [Backup and Recovery](backup_recovery.md)
- [Troubleshooting](../user_guide/troubleshooting.md)

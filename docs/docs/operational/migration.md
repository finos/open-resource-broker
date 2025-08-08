# Migration Procedures

This document provides detailed procedures for migrating between different storage strategies and configurations.

## Overview

The Open Host Factory Plugin supports migration between different storage backends and configuration formats. This guide covers:

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
ohfp storage migrate --source json --target sqlite

# Migrate with backup
ohfp storage migrate --source json --target sqlite --backup
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
   ohfp storage test
   ```

### Migration Steps

1. **Prepare target storage**
   ```bash
   # Initialize target storage
   ohfp storage init --type sqlite
   ```

2. **Run migration**
   ```bash
   # Execute migration
   ohfp storage migrate --source json --target sqlite --verify
   ```

3. **Verify migration**
   ```bash
   # Test target storage
   ohfp storage test --type sqlite

   # Compare data counts
   ohfp requests list --count
   ```

4. **Update configuration**
   ```bash
   # Update config to use new storage
   ohfp config update --storage-type sqlite
   ```

### Post-Migration Verification

After migration:

1. **Test basic operations**
   ```bash
   ohfp templates list
   ohfp requests list
   ```

2. **Verify data integrity**
   ```bash
   ohfp storage validate
   ```

3. **Test provider operations**
   ```bash
   ohfp providers health
   ```

## Configuration Migration

### Legacy Configuration Support

The plugin maintains backward compatibility with legacy configuration formats while supporting new formats.

### Migration Tools

Use configuration migration tools:

```bash
# Convert legacy config
ohfp config migrate --from legacy --to current

# Validate migrated config
ohfp config validate
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

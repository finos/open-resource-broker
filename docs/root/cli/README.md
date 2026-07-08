# CLI Documentation

Complete documentation for the Open Resource Broker command-line interface.

## Quick Reference

- **[CLI Reference](cli-reference.md)** - Complete command and flag reference
- **[Provider Override](provider-override.md)** - Using `--provider-name` flag functionality  
- **[Multi-Provider Templates](multi-provider-templates.md)** - Multi-provider template generation

## Detailed Guides

- **[Template Commands](template-commands.md)** - Template management operations
- **[Scheduler Commands](scheduler-commands.md)** - Scheduler management operations

## Key Features

### Global Overrides
- **`--scheduler`** - Override scheduler strategy for any command
- **`--provider-name`** - Override provider instance for any command
- **`--format`** - Control output format (json, yaml, table, list)

### Argument Patterns
- **Positional Arguments** - `orb templates show template-id`
- **Flag Arguments** - `orb templates show --template-id template-id`
- **Mixed Usage** - Both patterns work for most commands

### Multi-Provider Support
- **Default Generation** - `orb templates generate` (all active providers)
- **Specific Provider** - `orb templates generate --provider-name aws-prod`
- **Specific API** - `orb templates generate --provider-api EC2Fleet`

## Common Commands

### Setup and Configuration
```bash
orb init --scheduler hostfactory --provider-type aws --region us-east-1
orb config show --format yaml
orb system health --detailed
```

### Template Management
```bash
orb templates generate --all-providers
orb templates list --format table
orb templates show aws-basic --format yaml
```

### Machine Operations
```bash
orb machines request aws-basic 5
orb machines list --status running --format table
orb requests status req-123
```

### Provider Operations
```bash
orb providers list --detailed --format table
orb --provider-name aws-prod providers health
orb --provider-name aws-dev templates generate
```

## Getting Started

1. **Initialize Configuration**
   ```bash
   orb init
   ```

2. **Generate Templates**
   ```bash
   orb templates generate
   ```

3. **List Available Templates**
   ```bash
   orb templates list --format table
   ```

4. **Request Machines**
   ```bash
   orb machines request template-id 3
   ```

5. **Check Status**
   ```bash
   orb system health
   ```

## Advanced Usage

### Environment-Specific Operations
```bash
# Development
orb --provider-name aws-dev templates generate --provider-api SpotFleet
orb --provider-name aws-dev machines request spot-template 5

# Production  
orb --provider-name aws-prod templates generate --provider-api EC2Fleet
orb --provider-name aws-prod machines request prod-template 10
```

### Scheduler Comparison
```bash
# HostFactory format (IBM Symphony compatible)
orb --scheduler hostfactory templates list --format json

# Default format (CLI-friendly)
orb --scheduler default templates list --format table
```

### Batch Operations
```bash
# Multiple request status checks
orb requests status req-123 req-456 req-789

# Multiple machine operations
orb machines return i-123 i-456 i-789
```

## See Also

- [User Guide](../user_guide/) - General usage documentation
- [Configuration Guide](../configuration/) - Configuration management
- [API Reference](../api/) - REST API documentation
- [MCP Integration](../mcp/) - Model Context Protocol integration
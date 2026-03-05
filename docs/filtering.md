# Filtering Guide

## Generic Filtering

The ORB CLI supports generic filtering using the `--filter` flag with snake_case field names.

### Basic Syntax

```bash
orb <resource> list --filter "field_name=value"
orb <resource> list --filter "field_name~pattern"
orb <resource> list --filter "field_name=~regex"
```

### Filter Operators

- `=` - Exact match
- `~` - Contains (substring match)
- `=~` - Regex match
- `!=` - Not equal
- `!~` - Does not contain

### Machine Type Filtering

Use the `machine_types` field to filter by instance/VM types:

```bash
# Filter templates with t3 instance types
orb templates list --filter "machine_types~t3"

# Filter templates with medium-sized instances
orb templates list --filter "machine_types~medium"

# Filter templates with specific instance type
orb templates list --filter "machine_types~t3.large"

# Multiple filters (AND logic)
orb templates list --filter "machine_types~t3" --filter "provider_api=EC2Fleet"
```

### Common Filtering Examples

```bash
# Templates
orb templates list --filter "provider_api=EC2Fleet"
orb templates list --filter "machine_types~t3"
orb templates list --filter "template_id~instant"

# Machines
orb machines list --filter "status=running"
orb machines list --filter "machine_types~t3"
orb machines list --filter "template_id~fleet"

# Requests
orb requests list --filter "status=pending"
orb requests list --filter "template_id~spot"
orb requests list --filter "machine_types~large"
```

### Combining Filters

```bash
# Multiple generic filters (AND logic)
orb templates list --filter "machine_types~t3" --filter "provider_api=EC2Fleet"

# Generic + specific filters
orb machines list --filter "machine_types~t3" --status running

# Complex filtering
orb templates list --filter "machine_types~t3" --filter "template_id~instant" --provider-api EC2Fleet
```

### Field Names Reference

Use these snake_case field names for filtering:

- `machine_types` - Instance/VM types (replaces instance_type, vm_type)
- `machine_types_ondemand` - On-demand instance types
- `machine_types_priority` - Priority instance types
- `template_id` - Template identifier
- `provider_api` - Provider API (EC2Fleet, SpotFleet, ASG, RunInstances)
- `status` - Status (running, pending, stopped, etc.)
- `provider_name` - Provider instance name
- `provider_type` - Provider type (aws, provider1, provider2)

### Migration from Old Field Names

If you were using these old field names, update to the new unified field:

```bash
# OLD (deprecated)
--filter "instance_type=t3.medium"
--filter "vm_type=t3.medium"

# NEW (recommended)
--filter "machine_types~t3.medium"
```
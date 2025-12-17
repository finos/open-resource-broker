# Template Management Commands

The template management commands provide CRUD (Create, Read, Update, Delete) operations for compute templates in the Open Resource Broker. These commands use the TemplateConfigurationManager as the single source of truth.

## Available Commands

### List Templates

List all available templates:

```bash
orb templates list
```

List with detailed configuration fields:

```bash
orb templates list --long
```

Filter by provider API:

```bash
orb templates list --provider-api aws
```

Output formats:

```bash
orb templates list --format table
orb templates list --format yaml
orb templates list --format json
orb templates list --format list
```

### Show Template Details

Show detailed information about a specific template:

```bash
orb templates show TEMPLATE_ID
```

Show with different output formats:

```bash
orb templates show my-template --format yaml
orb templates show my-template --format table
```

### Create Template

Create a new template from a configuration file:

```bash
orb templates create --file template.json
```

Create with validation only (don't save):

```bash
orb templates create --file template.json --validate-only
```

#### Template Configuration File Format

Templates can be defined in JSON or YAML format:

**JSON Format:**
```json
{
  "template_id": "my-aws-template",
  "name": "My AWS Template",
  "provider_api": "aws",
  "image_id": "ami-12345678",
  "instance_type": "t3.medium",
  "key_name": "my-keypair",
  "security_group_ids": ["sg-12345678"],
  "subnet_ids": ["subnet-12345678"],
  "user_data": "#!/bin/bash\necho 'Hello World'",
  "tags": {
    "Environment": "development",
    "Project": "hostfactory"
  },
  "version": "1.0"
}
```

**YAML Format:**
```yaml
template_id: my-aws-template
name: My AWS Template
provider_api: aws
image_id: ami-12345678
instance_type: t3.medium
key_name: my-keypair
security_group_ids:
  - sg-12345678
subnet_ids:
  - subnet-12345678
user_data: |
  #!/bin/bash
  echo 'Hello World'
tags:
  Environment: development
  Project: hostfactory
version: "1.0"
```

### Update Template

Update an existing template:

```bash
orb templates update TEMPLATE_ID --file updated-template.json
```

### Delete Template

Delete a template:

```bash
orb templates delete TEMPLATE_ID
```

Force deletion without confirmation:

```bash
orb templates delete TEMPLATE_ID --force
```

### Validate Template

Validate a template configuration file:

```bash
orb templates validate --file template.json
```

### Refresh Templates

Refresh the template cache and reload from files:

```bash
orb templates refresh
```

Force complete refresh:

```bash
orb templates refresh --force
```

## Template File Hierarchy

The system supports provider-specific template files with hierarchical loading:

1. **Instance Files**: `{provider}inst_templates.json` (Priority 1)
2. **Type Files**: `{provider}type_templates.json` (Priority 2)
3. **Main Files**: `{provider}prov_templates.json` (Priority 3)
4. **Legacy Files**: `templates.json` (Priority 4)

Templates are merged with higher priority files overriding lower priority ones.

## Examples

### Basic Usage

```bash
# List all templates
$ orb templates list
{
  "success": true,
  "templates": [
    {
      "template_id": "aws-basic",
      "name": "Basic AWS Template",
      "provider_api": "aws",
      "image_id": "ami-12345678",
      "instance_type": "t3.medium"
    }
  ],
  "total_count": 1,
  "message": "Retrieved 1 templates successfully"
}

# Show specific template
$ orb templates show aws-basic
{
  "success": true,
  "template": {
    "template_id": "aws-basic",
    "name": "Basic AWS Template",
    "provider_api": "aws",
    "image_id": "ami-12345678",
    "instance_type": "t3.medium",
    "key_name": "my-keypair",
    "security_group_ids": ["sg-12345678"],
    "subnet_ids": ["subnet-12345678"],
    "tags": {
      "Environment": "production"
    }
  },
  "message": "Retrieved template aws-basic successfully"
}
```

### Template Creation

```bash
# Create template from file
$ orb templates create --file new-template.json
{
  "success": true,
  "message": "Template created successfully",
  "template_id": "new-aws-template"
}

# Validate template without creating
$ orb templates create --file new-template.json --validate-only
{
  "success": true,
  "message": "Template validation successful",
  "template_id": "new-aws-template"
}
```

### Template Validation

```bash
# Validate template file
$ orb templates validate --file template.json
{
  "success": true,
  "valid": true,
  "validation_errors": [],
  "validation_warnings": [],
  "template_id": "my-template",
  "message": "Validation completed"
}

# Validation with errors
$ orb templates validate --file invalid-template.json
{
  "success": false,
  "valid": false,
  "validation_errors": [
    "Missing required field: template_id",
    "Invalid instance_type: must be valid EC2 instance type"
  ],
  "validation_warnings": [
    "No tags specified: recommended for resource management"
  ],
  "message": "Validation failed"
}
```

### Template Management

```bash
# Update template
$ orb templates update aws-basic --file updated-template.json
{
  "success": true,
  "message": "Template updated successfully",
  "template_id": "aws-basic"
}

# Delete template
$ orb templates delete old-template
{
  "success": true,
  "message": "Template deleted successfully",
  "template_id": "old-template"
}

# Refresh template cache
$ orb templates refresh
{
  "success": true,
  "message": "Templates refreshed successfully",
  "template_count": 5,
  "cache_stats": {
    "cache_hits": 0,
    "cache_misses": 5,
    "files_loaded": 3
  }
}
```

### Table Format Output

```bash
$ orb templates list --format table
+---------------+--------------------+------------+---------------+-------------+
│ Template ID     │ Name                 │ Provider API │ Image ID        │ Instance Type │
+---------------+--------------------+------------+---------------+-------------+
│ aws-basic       │ Basic AWS Template   │ aws          │ ami-12345678    │ t3.medium     │
│ aws-spot        │ Spot Instance Temp   │ aws          │ ami-87654321    │ t3.large      │
│ aws-gpu         │ GPU Instance Temp    │ aws          │ ami-11111111    │ p3.2xlarge    │
+---------------+--------------------+------------+---------------+-------------+
```

## Error Handling

The template commands provide detailed error messages for common issues:

### Template Not Found

```bash
$ orb templates show nonexistent-template
{
  "success": false,
  "error": "Template not found: nonexistent-template",
  "template": null
}
```

### Invalid Template Configuration

```bash
$ orb templates create --file invalid.json
{
  "success": false,
  "error": "Invalid template configuration: Missing required field 'template_id'"
}
```

### File Access Issues

```bash
$ orb templates create --file missing-file.json
{
  "success": false,
  "error": "Template file not found: missing-file.json"
}
```

## Integration with Other Commands

Template commands integrate seamlessly with other CLI operations:

- **Machine Requests**: Use template IDs from `templates list` in `machines request`
- **Provider Operations**: Templates are provider-specific and work with provider commands
- **Scheduler Integration**: Templates work with all scheduler strategies via global `--scheduler` flag

### Global Scheduler Override

```bash
# Use different scheduler for template operations
orb --scheduler default templates list
orb --scheduler hostfactory templates show aws-basic
```

## Additional Features

### Caching

The template system includes effective caching:

- **File Modification Tracking**: Automatically detects file changes
- **TTL-based Expiration**: Configurable cache timeout
- **Manual Refresh**: Force cache refresh with `templates refresh`

### Provider-Specific Files

Templates are organized by provider with hierarchical loading:

```
config/
├── awsinst_templates.json    # AWS instance-specific (highest priority)
├── awstype_templates.json    # AWS type-specific
├── awsprov_templates.json    # AWS provider templates
└── templates.json            # Legacy templates (lowest priority)
```

### Template Merging

Templates from multiple files are merged with priority:
1. Higher priority files override lower priority
2. Individual fields are merged (not whole templates)
3. Arrays and objects are merged intelligently

This allows for flexible template organization and inheritance patterns.
# CLI Reference

Complete reference for the Open Resource Broker command-line interface.

## Global Options

Available for all commands:

| Flag | Description | Example |
|------|-------------|---------|
| `--config` | Configuration file path | `--config /path/to/config.json` |
| `--log-level` | Set logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) | `--log-level DEBUG` |
| `--format` | Output format (`json`, `yaml`, `table`, `list`) | `--format table` |
| `--output` | Write output to file instead of stdout | `--output results.json` |
| `--quiet` | Suppress non-essential output | `--quiet` |
| `--verbose` | Enable verbose output | `--verbose` |
| `--no-color` | Disable colored output | `--no-color` |
| `--dry-run` | Show what would be done without executing | `--dry-run` |
| `--yes, -y` | Assume yes to all prompts | `--yes` |
| `--all` | Apply operation to all resources | `--all` |
| `--limit` | Maximum number of results to return | `--limit 50` |
| `--offset` | Number of results to skip (for pagination) | `--offset 100` |
| `--filter` | Filter using `field=value`, `field~value`, or `field=~regex`. Repeatable (AND logic). | `--filter "status=running"` |
| `--region` | AWS region override | `--region us-west-2` |
| `--profile` | AWS profile override | `--profile production` |
| `--scheduler` | Override scheduler strategy (`default`, `hostfactory`, `hf`) | `--scheduler hostfactory` |
| `--provider` | Override provider instance | `--provider aws_prod_us-east-1` |
| `--completion` | Generate shell completion script (`bash`, `zsh`) | `--completion bash` |
| `--version` | Show version and exit | `--version` |

### HostFactory Compatibility

| Flag | Description | Example |
|------|-------------|---------|
| `-f, --file` | Input JSON file path | `-f request.json` |
| `-d, --data` | Input JSON data string | `-d '{"template":"aws-basic"}'` |

## Commands by Resource

### Templates

Manage compute templates.

#### `templates list`

List all available templates.

**Usage:**
```bash
orb templates list [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--provider-api` | Filter by provider API type | `--provider-api EC2Fleet` |
| `--long` | Include detailed configuration fields | `--long` |
| `--format` | Output format (json, yaml, table, list) | `--format table` |

**Examples:**
```bash
# List all templates
orb templates list

# List with details in table format
orb templates list --long --format table

# Filter by provider API
orb templates list --provider-api EC2Fleet
```

#### `templates show`

Show detailed information about a specific template.

**Usage:**
```bash
orb templates show [TEMPLATE_ID] [OPTIONS]
orb templates show --template-id TEMPLATE_ID [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `TEMPLATE_ID` | Template ID to show (positional) | Yes* |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--template-id, -t` | Template ID to show (flag) | `--template-id aws-basic` |
| `--format` | Output format | `--format yaml` |

**Examples:**
```bash
# Using positional argument
orb templates show aws-basic

# Using flag
orb templates show --template-id aws-basic

# With YAML output
orb templates show aws-basic --format yaml
```

#### `templates create`

Create a new template from a configuration file.

**Usage:**
```bash
orb templates create --file FILE [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--file` | Template configuration file | `--file template.json` |
| `--validate-only` | Only validate, do not create | `--validate-only` |

**Examples:**
```bash
# Create template
orb templates create --file new-template.json

# Validate only
orb templates create --file template.json --validate-only
```

#### `templates update`

Update an existing template.

**Usage:**
```bash
orb templates update [TEMPLATE_ID] --file FILE [OPTIONS]
orb templates update --template-id TEMPLATE_ID --file FILE [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `TEMPLATE_ID` | Template ID to update (positional) | Yes* |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--template-id, -t` | Template ID to update (flag) | `--template-id aws-basic` |
| `--file` | Updated template configuration file | `--file updated.json` |

**Examples:**
```bash
# Using positional argument
orb templates update aws-basic --file updated.json

# Using flag
orb templates update --template-id aws-basic --file updated.json
```

#### `templates delete`

Delete a template.

**Usage:**
```bash
orb templates delete [TEMPLATE_ID] [OPTIONS]
orb templates delete --template-id TEMPLATE_ID [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `TEMPLATE_ID` | Template ID to delete (positional) | Yes* |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--template-id, -t` | Template ID to delete (flag) | `--template-id old-template` |
| `--force` | Force deletion without confirmation | `--force` |

**Examples:**
```bash
# Using positional argument
orb templates delete old-template

# Using flag with force
orb templates delete --template-id old-template --force
```

#### `templates validate`

Validate a template configuration file.

**Usage:**
```bash
orb templates validate --file FILE
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--file` | Template file to validate | `--file template.json` |

#### `templates refresh`

Refresh template cache and reload from files.

**Usage:**
```bash
orb templates refresh [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--force` | Force complete refresh | `--force` |

#### `templates generate`

Generate example templates for providers.

**Usage:**
```bash
orb templates generate [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--provider` | Generate for specific provider instance | `--provider aws_prod_us-east-1` |
| `--all-providers` | Explicitly generate for all active providers | `--all-providers` |
| `--provider-api` | Specific provider API | `--provider-api EC2Fleet` |

**Examples:**
```bash
# Generate for all active providers (default)
orb templates generate

# Generate for specific provider
orb templates generate --provider aws_prod_us-east-1

# Generate for specific API
orb templates generate --provider-api SpotFleet
```

### Machines

Manage compute instances.

#### `machines list`

List all machines.

**Usage:**
```bash
orb machines list [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--status` | Filter by machine status (specific filter) | `--status running` |
| `--template-id` | Filter by template ID (specific filter) | `--template-id aws-basic` |
| `--timestamp-format` | Timestamp format: `auto`, `unix`, `iso` | `--timestamp-format iso` |
| `--filter` | Generic filter: `field=value`, `field~value`, `field=~regex`. Repeatable (AND logic). | `--filter "machine_types~t3"` |
| `--format` | Output format | `--format table` |

**Filter Examples:**
```bash
# Filter by machine type substring
orb machines list --filter "machine_types~t3"

# Exact match on a field
orb machines list --filter "status=running"

# Regex match
orb machines list --filter "machine_id=~i-[0-9a-f]{17}"

# Combine multiple filters (AND logic)
orb machines list --filter "status=running" --filter "machine_types~t3"

# Mix specific and generic filters
orb machines list --status running --filter "machine_types~t3"
```

#### `machines show`

Show machine details.

**Usage:**
```bash
orb machines show [MACHINE_ID] [OPTIONS]
orb machines show --machine-id MACHINE_ID [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `MACHINE_ID` | Machine ID to show (positional) | Yes* |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--machine-id, -m` | Machine ID to show (flag) | `--machine-id i-1234567890abcdef0` |
| `--format` | Output format | `--format yaml` |

#### `machines request`

Request new machines.

**Usage:**
```bash
orb machines request [TEMPLATE_ID] [COUNT] [OPTIONS]
orb machines request --template-id TEMPLATE_ID --count COUNT [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `TEMPLATE_ID` | Template ID to use (positional) | Yes* |
| `COUNT` | Number of machines to request (positional) | Yes* |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--template-id, -t` | Template ID to use (flag) | `--template-id aws-basic` |
| `--count, -c` | Number of machines to request (flag) | `--count 5` |
| `--wait` | Wait for machines to be ready | `--wait` |
| `--timeout` | Wait timeout in seconds | `--timeout 600` |

**Examples:**
```bash
# Using positional arguments
orb machines request aws-basic 3

# Using flags
orb machines request --template-id aws-basic --count 3

# With wait and timeout
orb machines request aws-basic 5 --wait --timeout 600
```

#### `machines return`

Return (terminate) machines.

**Usage:**
```bash
orb machines return [MACHINE_IDS...] [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `MACHINE_IDS` | Machine IDs to return (positional) | Yes* |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--force` | Force return without confirmation | `--force` |

#### `machines status`

Check machine status.

**Usage:**
```bash
orb machines status MACHINE_IDS...
orb machines status --machine-id MACHINE_ID [--machine-id MACHINE_ID...] [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `MACHINE_IDS` | Machine IDs to check (positional) | Yes* |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--machine-id, -m` | Machine ID to check (flag, repeatable) | `--machine-id i-abc --machine-id i-def` |

#### `machines terminate`

Terminate (return) machines. Alias for `machines return`.

**Usage:**
```bash
orb machines terminate [MACHINE_IDS...] [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `MACHINE_IDS` | Machine IDs to terminate (positional) | Yes* |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--force` | Force termination without confirmation | `--force` |

**Examples:**
```bash
orb machines terminate i-1234567890abcdef0
orb machines terminate i-aaa i-bbb --force
```

#### `machines stop`

Stop running machines.

**Usage:**
```bash
orb machines stop [MACHINE_IDS...] [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `MACHINE_IDS` | Machine IDs to stop (positional) | Yes* |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--force` | Force stop without confirmation | `--force` |

**Examples:**
```bash
orb machines stop i-1234567890abcdef0
orb machines stop i-aaa i-bbb --force
```

#### `machines start`

Start stopped machines.

**Usage:**
```bash
orb machines start [MACHINE_IDS...] [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `MACHINE_IDS` | Machine IDs to start (positional) | Yes* |

**Examples:**
```bash
orb machines start i-1234567890abcdef0
orb machines start i-aaa i-bbb
```

### Requests

Manage provisioning requests.

#### `requests list`

List all requests.

**Usage:**
```bash
orb requests list [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--status` | Filter by request status (specific filter) | `--status pending` |
| `--template-id` | Filter by template ID (specific filter) | `--template-id aws-basic` |
| `--filter` | Generic filter: `field=value`, `field~value`, `field=~regex`. Repeatable (AND logic). | `--filter "template_id~spot"` |
| `--format` | Output format | `--format table` |

**Status Values:**
- `pending` - Request submitted but not started
- `in_progress` - Request being processed
- `completed` - Request completed successfully
- `failed` - Request failed
- `cancelled` - Request cancelled
- `partial` - Request partially completed
- `timeout` - Request timed out

**Filter Examples:**
```bash
# Filter by status
orb requests list --status pending

# Generic filter by template ID substring
orb requests list --filter "template_id~spot"

# Combine filters (AND logic)
orb requests list --status in_progress --filter "template_id~ec2fleet"
```

#### `requests show`

Show details for a specific request.

**Usage:**
```bash
orb requests show REQUEST_ID [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `REQUEST_ID` | Request ID to show (positional) | Yes* |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format yaml` |

**Examples:**
```bash
orb requests show req-abc123
orb requests show req-abc123 --format yaml
```

#### `requests cancel`

Cancel a request.

**Usage:**
```bash
orb requests cancel REQUEST_ID [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `REQUEST_ID` | Request ID to cancel | Yes |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--force` | Force cancellation | `--force` |

#### `requests status`

Check request status.

**Usage:**
```bash
orb requests status [REQUEST_IDS...] [OPTIONS]
orb requests status --request-id REQUEST_ID [--request-id REQUEST_ID...] [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `REQUEST_IDS` | Request IDs to check (positional) | Yes* |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--request-id, -r` | Request ID to check (flag, repeatable) | `--request-id req-123 --request-id req-456` |

**Examples:**
```bash
# Using positional arguments
orb requests status req-123 req-456

# Using flags
orb requests status --request-id req-123 --request-id req-456

# Mixed usage
orb requests status req-123 --request-id req-456
```

### System

System operations and health checks.

#### `system status`

Show system status.

**Usage:**
```bash
orb system status [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format table` |

#### `system health`

Run health check.

**Usage:**
```bash
orb system health [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--detailed` | Show detailed health information | `--detailed` |

#### `system metrics`

Show system metrics.

**Usage:**
```bash
orb system metrics [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format table` |

#### `system serve`

Start REST API server.

**Usage:**
```bash
orb system serve [OPTIONS]
```

**Options:**
| Flag | Description | Default | Example |
|------|-------------|---------|---------|
| `--host` | Server host | `0.0.0.0` | `--host localhost` |
| `--port` | Server port | `8000` | `--port 9000` |
| `--workers` | Number of workers | `1` | `--workers 4` |
| `--reload` | Enable auto-reload | `false` | `--reload` |
| `--server-log-level` | Server log level | `info` | `--server-log-level debug` |

### Config

Configuration management.

#### `config show`

Show configuration.

**Usage:**
```bash
orb config show [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format yaml` |

#### `config set`

Set configuration value.

**Usage:**
```bash
orb config set KEY VALUE
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `KEY` | Configuration key | Yes |
| `VALUE` | Configuration value | Yes |

#### `config get`

Get configuration value.

**Usage:**
```bash
orb config get KEY
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `KEY` | Configuration key | Yes |

#### `config validate`

Validate configuration.

**Usage:**
```bash
orb config validate [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--file` | Configuration file to validate | `--file config.json` |

### Providers

Provider management and operations.

#### `providers list`

List available providers.

**Usage:**
```bash
orb providers list [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format table` |
| `--detailed` | Show detailed provider information | `--detailed` |

#### `providers show`

Show provider details.

**Usage:**
```bash
orb providers show [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format yaml` |
| `--provider` | Show specific provider details | `--provider aws_prod_us-east-1` |

#### `providers health`

Check provider health.

**Usage:**
```bash
orb providers health [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format table` |
| `--provider` | Check specific provider health | `--provider aws_prod_us-east-1` |

#### `providers add`

Add a new provider instance.

**Usage:**
```bash
orb providers add [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--name` | Provider instance name | `--name aws-prod` |
| `--aws-profile` | AWS profile name | `--aws-profile production` |
| `--aws-region` | AWS region | `--aws-region us-east-1` |
| `--discover` | Discover infrastructure after adding | `--discover` |

**Examples:**
```bash
orb providers add --name aws-prod --aws-profile production --aws-region us-east-1
orb providers add --name aws-dev --aws-profile dev --aws-region us-west-2 --discover
```

#### `providers remove`

Remove a provider instance.

**Usage:**
```bash
orb providers remove PROVIDER_NAME [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `PROVIDER_NAME` | Provider instance name to remove | Yes |

**Examples:**
```bash
orb providers remove aws-dev
```

#### `providers update`

Update provider configuration.

**Usage:**
```bash
orb providers update PROVIDER_NAME [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `PROVIDER_NAME` | Provider instance name | Yes |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--aws-region` | Update region | `--aws-region eu-west-1` |
| `--aws-profile` | Update profile | `--aws-profile new-profile` |

**Examples:**
```bash
orb providers update aws-prod --aws-region eu-west-1
orb providers update aws-prod --aws-profile updated-profile
```

#### `providers set-default`

Set the default provider instance.

**Usage:**
```bash
orb providers set-default PROVIDER_NAME
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `PROVIDER_NAME` | Provider name to set as default | Yes |

**Examples:**
```bash
orb providers set-default aws-prod
```

#### `providers get-default`

Show the current default provider.

**Usage:**
```bash
orb providers get-default [OPTIONS]
```

**Examples:**
```bash
orb providers get-default
orb providers get-default --format yaml
```

#### `providers select`

Select provider strategy.

**Usage:**
```bash
orb providers select PROVIDER [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `PROVIDER` | Provider name to select | Yes |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--strategy` | Specific strategy to select | `--strategy ec2fleet` |

#### `providers exec`

Execute provider operation.

**Usage:**
```bash
orb providers exec OPERATION [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `OPERATION` | Operation to execute | Yes |

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--provider` | Provider to execute operation on | `--provider aws_prod_us-east-1` |
| `--params` | Operation parameters (JSON format) | `--params '{"count":5}'` |

#### `providers metrics`

Show provider metrics.

**Usage:**
```bash
orb providers metrics [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format table` |
| `--provider` | Show metrics for specific provider | `--provider aws_prod_us-east-1` |

### Storage

Storage management and operations.

#### `storage list`

List available storage strategies.

**Usage:**
```bash
orb storage list [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format table` |

#### `storage show`

Show current storage configuration.

**Usage:**
```bash
orb storage show [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format yaml` |
| `--strategy` | Show specific storage strategy details | `--strategy json` |

#### `storage validate`

Validate storage configuration.

**Usage:**
```bash
orb storage validate [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--strategy` | Validate specific storage strategy | `--strategy json` |

#### `storage test`

Test storage connectivity.

**Usage:**
```bash
orb storage test [OPTIONS]
```

**Options:**
| Flag | Description | Default | Example |
|------|-------------|---------|---------|
| `--strategy` | Test specific storage strategy | | `--strategy json` |
| `--timeout` | Test timeout in seconds | `30` | `--timeout 60` |

#### `storage health`

Check storage health.

**Usage:**
```bash
orb storage health [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format table` |
| `--detailed` | Show detailed health information | `--detailed` |

#### `storage metrics`

Show storage performance metrics.

**Usage:**
```bash
orb storage metrics [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format table` |
| `--strategy` | Show metrics for specific storage strategy | `--strategy json` |

### Scheduler

Scheduler management and configuration.

#### `scheduler list`

List available scheduler strategies.

**Usage:**
```bash
orb scheduler list [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format table` |
| `--long` | Show detailed information | `--long` |

#### `scheduler show`

Show scheduler configuration.

**Usage:**
```bash
orb scheduler show [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format yaml` |
| `--scheduler` | Show specific scheduler strategy details | `--scheduler hostfactory` |

#### `scheduler validate`

Validate scheduler configuration.

**Usage:**
```bash
orb scheduler validate [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--format` | Output format | `--format table` |
| `--scheduler` | Validate specific scheduler strategy | `--scheduler default` |

### MCP (Model Context Protocol)

MCP operations for AI assistant integration.

#### `mcp tools list`

List available MCP tools.

**Usage:**
```bash
orb mcp tools list [OPTIONS]
```

**Options:**
| Flag | Description | Default | Example |
|------|-------------|---------|---------|
| `--format` | Output format | `table` | `--format json` |
| `--type` | Filter tools by handler type | | `--type command` |

#### `mcp tools call`

Call MCP tool directly.

**Usage:**
```bash
orb mcp tools call TOOL_NAME [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `TOOL_NAME` | Name of tool to call | Yes |

**Options:**
| Flag | Description | Default | Example |
|------|-------------|---------|---------|
| `--args` | Tool arguments as JSON string | | `--args '{"template_id":"aws-basic"}'` |
| `--file` | Tool arguments from JSON file | | `--file args.json` |
| `--format` | Output format | `json` | `--format yaml` |

#### `mcp tools info`

Get information about MCP tool.

**Usage:**
```bash
orb mcp tools info TOOL_NAME [OPTIONS]
```

**Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `TOOL_NAME` | Name of tool to get info for | Yes |

**Options:**
| Flag | Description | Default | Example |
|------|-------------|---------|---------|
| `--format` | Output format | `table` | `--format json` |

#### `mcp validate`

Validate MCP configuration.

**Usage:**
```bash
orb mcp validate [OPTIONS]
```

**Options:**
| Flag | Description | Default | Example |
|------|-------------|---------|---------|
| `--config` | MCP configuration file to validate | | `--config mcp.json` |
| `--format` | Output format | `table` | `--format json` |

#### `mcp serve`

Start MCP server.

**Usage:**
```bash
orb mcp serve [OPTIONS]
```

**Options:**
| Flag | Description | Default | Example |
|------|-------------|---------|---------|
| `--port` | Server port | `3000` | `--port 4000` |
| `--host` | Server host | `localhost` | `--host 0.0.0.0` |
| `--stdio` | Run in stdio mode for direct MCP client communication | `false` | `--stdio` |
| `--log-level` | Logging level for MCP server | `INFO` | `--log-level DEBUG` |

### Infrastructure

Discover and manage AWS infrastructure (VPCs, subnets, security groups) used by ORB.

See [infrastructure-commands.md](./infrastructure-commands.md) for full documentation.

#### `infrastructure discover`

Scan your AWS account for available VPCs, subnets, and security groups.

**Usage:**
```bash
orb infrastructure discover [OPTIONS]
orb infra discover [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--show` | Show specific resources: `vpcs`, `subnets`, `security-groups` (or `sg`), or `all` | `--show subnets` |
| `--summary` | Show only summary counts, no details | `--summary` |
| `--all-providers` | Discover across all providers | `--all-providers` |

#### `infrastructure show`

Display the infrastructure ORB is currently configured to use.

**Usage:**
```bash
orb infrastructure show [OPTIONS]
orb infra show [OPTIONS]
```

#### `infrastructure validate`

Verify that the configured infrastructure still exists in AWS.

**Usage:**
```bash
orb infrastructure validate [OPTIONS]
orb infra validate [OPTIONS]
```

### Init

Initialize ORB configuration.

#### `init`

Initialize ORB configuration.

**Usage:**
```bash
orb init [OPTIONS]
```

**Options:**
| Flag | Description | Example |
|------|-------------|---------|
| `--non-interactive` | Non-interactive mode | `--non-interactive` |
| `--force` | Force overwrite existing config | `--force` |
| `--scheduler` | Scheduler type | `--scheduler hostfactory` |
| `--provider` | Provider type | `--provider aws` |
| `--region` | AWS region | `--region us-west-2` |
| `--profile` | AWS profile | `--profile production` |
| `--config-dir` | Custom configuration directory | `--config-dir /custom/config` |
| `--subnet-ids` | Comma-separated subnet IDs for `template_defaults` (non-interactive only) | `--subnet-ids subnet-aaa,subnet-bbb` |
| `--security-group-ids` | Comma-separated security group IDs for `template_defaults` (non-interactive only) | `--security-group-ids sg-111,sg-222` |
| `--fleet-role` | Spot Fleet IAM role ARN or name for `template_defaults` (non-interactive only) | `--fleet-role arn:aws:iam::123456789012:role/SpotFleetRole` |

**Notes:**
- `--subnet-ids`, `--security-group-ids`, and `--fleet-role` are only used with `--non-interactive`. In interactive mode, ORB discovers these values from your AWS account automatically.
- These values are stored in `template_defaults` in `config.json` and applied at runtime when provisioning machines — they are not baked into template files.

**Examples:**
```bash
# Interactive init (discovers infrastructure from AWS)
orb init --scheduler hostfactory --provider aws --region us-east-1

# Non-interactive init with pre-known infrastructure values
orb init --non-interactive --region us-east-1 \
  --subnet-ids subnet-aaa111,subnet-bbb222 \
  --security-group-ids sg-11111111 \
  --fleet-role arn:aws:iam::123456789012:role/SpotFleetRole
```

## Argument Patterns

### Positional vs Flag Arguments

Many commands support both positional and flag arguments for flexibility:

#### Templates
```bash
# Both work the same
orb templates show aws-basic
orb templates show --template-id aws-basic

orb templates delete old-template
orb templates delete --template-id old-template
```

#### Machines
```bash
# Both work the same
orb machines show i-1234567890abcdef0
orb machines show --machine-id i-1234567890abcdef0

orb machines request aws-basic 5
orb machines request --template-id aws-basic --count 5
```

#### Requests
```bash
# Multiple request IDs - both patterns work
orb requests status req-123 req-456 req-789
orb requests status --request-id req-123 --request-id req-456 --request-id req-789

# Mixed usage also works
orb requests status req-123 --request-id req-456
```

### Required Arguments

Arguments marked with `*` in the tables above are required, but can be provided either as positional arguments OR as flags (not both).

## Global Overrides

### Scheduler Override

Use `--scheduler` to override the configured scheduler for any command:

```bash
# Use HostFactory scheduler for this command
orb --scheduler hostfactory templates list

# Use default scheduler for this command  
orb --scheduler default machines request aws-basic 3

# Short alias for HostFactory
orb --scheduler hf requests status req-123
```

**Available Schedulers:**
- `default` - Native domain format, CLI-friendly output
- `hostfactory` - IBM Symphony HostFactory compatible format
- `hf` - Alias for `hostfactory`

### Provider Override

Use `--provider` to override the selected provider instance for any command:

```bash
# Use specific provider instance
orb --provider aws_prod_us-east-1 templates list

# Override for machine requests
orb --provider aws_dev_us-east-1 machines request template-id 3

# Combined with scheduler override
orb --scheduler hostfactory --provider aws_prod_us-east-1 requests status req-123
```

**Provider Instance Names:**
Provider instances are defined in your configuration file. Common patterns:
- `aws_prod_us-east-1` - AWS production profile in US East 1
- `aws_dev_us-east-1` - AWS development profile in US East 1
- `aws_staging_us-west-2` - AWS staging profile in US West 2
- `aws_default_eu-west-1` - AWS default profile in EU West 1

Use `orb providers list` to see available provider instances.

## Multi-Provider Template Generation

The `templates generate` command supports generating templates for multiple providers:

### Default Behavior
```bash
# Generates templates for ALL active providers
orb templates generate
```

### Provider-Specific Generation
```bash
# Generate for specific provider instance
orb templates generate --provider aws_prod_us-east-1

# Generate for specific provider API
orb templates generate --provider-api EC2Fleet

# Explicitly generate for all providers
orb templates generate --all-providers
```

### Provider Naming Conventions

Templates are generated using provider-specific naming patterns:

**AWS Provider Pattern:** `{type}_{profile}_{region}`
- Example: `aws_prod_us-west-2`
- Components: provider type + AWS profile + AWS region

**Template Files Generated:**
- `{provider-name}_templates.json` - Main template file
- One file per active provider instance

## Output Formats

All commands support multiple output formats via `--format`:

| Format | Description | Best For |
|--------|-------------|----------|
| `json` | JSON format (default) | Scripting, APIs |
| `yaml` | YAML format | Human-readable config |
| `table` | Rich table format | Terminal viewing |
| `list` | Simple list format | Quick scanning |

**Examples:**
```bash
# JSON output (default)
orb templates list

# Table format for terminal
orb templates list --format table

# YAML for configuration
orb config show --format yaml

# List for simple output
orb providers list --format list
```

## Exit Codes

Commands return appropriate exit codes for scripting:

| Code | Meaning | Example |
|------|---------|---------|
| `0` | Success | Request completed, command succeeded |
| `1` | Failure | Request failed, validation error, command error |

**Scheduler-Aware Exit Codes:**
- **HostFactory:** Returns 1 for failed/cancelled/timeout/partial requests
- **Default:** Returns 1 for any problem status

## Environment Variables

### Standard ORB Variables
- `ORB_CONFIG_DIR` - Configuration directory
- `ORB_WORK_DIR` - Working directory  
- `ORB_LOG_DIR` - Log directory
- `ORB_LOG_LEVEL` - Log level (DEBUG, INFO, WARNING, ERROR)

### HostFactory Variables (when using HostFactory scheduler)
- `HF_PROVIDER_CONFDIR` - HostFactory config directory
- `HF_PROVIDER_WORKDIR` - HostFactory work directory
- `HF_PROVIDER_LOGDIR` - HostFactory log directory
- `HF_LOGLEVEL` - HostFactory log level
- `HF_LOGGING_CONSOLE_ENABLED` - Enable/disable console logging

## Shell Completion

Generate shell completion scripts:

```bash
# Bash completion
orb --completion bash > /etc/bash_completion.d/orb

# Zsh completion  
orb --completion zsh > ~/.zsh/completions/_orb
```

## Examples

### Basic Workflow
```bash
# Initialize configuration
orb init --scheduler hostfactory --provider aws --region us-east-1

# Generate example templates
orb templates generate

# List available templates
orb templates list --format table

# Request machines
orb machines request aws-basic 3

# Check request status
orb requests status req-123

# Check system health
orb system health --detailed
```

### Multi-Provider Setup
```bash
# Generate templates for all providers
orb templates generate --all-providers

# Use specific provider
orb --provider aws_prod_us-east-1 machines request template-id 5

# Check provider health
orb providers health --provider aws_prod_us-east-1
```

### Development Workflow
```bash
# Use different scheduler for testing
orb --scheduler default templates list

# Validate configuration
orb config validate

# Test storage connectivity
orb storage test --timeout 60

# Start API server for development
orb system serve --reload --port 9000
```
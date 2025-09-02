# HostFactory Feature Mapping

This document maps IBM Spectrum Symphony Host Factory features to Open Host Factory Plugin capabilities, showing how each HostFactory requirement is implemented.

## Core Feature Mapping

### Template Management

| HostFactory Feature | Plugin Implementation | Status |
|---------------------|----------------------|--------|
| Template Discovery | `python src/run.py templates list` | [IMPLEMENTED] Implemented |
| Template Validation | `python src/run.py templates validate` | [IMPLEMENTED] Implemented |
| Template Attributes | JSON-based attribute system | [IMPLEMENTED] Implemented |
| Template Limits | `maxNumber` field support | [IMPLEMENTED] Implemented |
| Template Types | Multi-provider template support | [IMPLEMENTED] Implemented |

**HostFactory Template Structure**:
```json
{
  "templateId": "basic-template",
  "maxNumber": 10,
  "attributes": {
    "type": ["String", "X86_64"],
    "ncpus": ["Numeric", "2"],
    "nram": ["Numeric", "4096"]
  }
}
```

**Plugin Template Implementation**:
- **Storage**: JSON-based template storage with validation
- **Discovery**: Dynamic template discovery from configuration
- **Validation**: Schema-based template validation
- **Extensibility**: Support for custom attributes

### Machine Provisioning

| HostFactory Feature | Plugin Implementation | Status |
|---------------------|----------------------|--------|
| Machine Requests | `python src/run.py machines create` | [IMPLEMENTED] Implemented |
| Batch Provisioning | Multi-machine request support | [IMPLEMENTED] Implemented |
| Async Provisioning | Request-based async model | [IMPLEMENTED] Implemented |
| Status Tracking | Request status monitoring | [IMPLEMENTED] Implemented |
| Resource Limits | Template-based limits | [IMPLEMENTED] Implemented |

**HostFactory Request Flow**:
1. `requestMachines`  ->  Create provisioning request
2. `getRequestStatus`  ->  Monitor provisioning progress
3. Machines become available when status = "completed"
4. `requestReturnMachines`  ->  Terminate machines

**Plugin Request Implementation**:
- **Request Management**: Persistent request tracking
- **State Machine**: Complete request lifecycle management
- **Provider Integration**: AWS EC2Fleet, SpotFleet, ASG support
- **Error Handling**: Comprehensive error recovery

### Machine Lifecycle

| HostFactory Feature | Plugin Implementation | Status |
|---------------------|----------------------|--------|
| Machine States | Complete state machine | [IMPLEMENTED] Implemented |
| State Transitions | Validated state changes | [IMPLEMENTED] Implemented |
| Machine Metadata | Rich machine information | [IMPLEMENTED] Implemented |
| IP Address Assignment | Network configuration | [IMPLEMENTED] Implemented |
| Machine Termination | Graceful shutdown | [IMPLEMENTED] Implemented |

**Machine States Mapping**:

| HostFactory State | Plugin State | AWS State | Description |
|-------------------|--------------|-----------|-------------|
| `pending` | `pending` | `pending` | Machine being created |
| `running` | `running` | `running` | Machine is active |
| `terminating` | `terminating` | `shutting-down` | Machine being terminated |
| `terminated` | `terminated` | `terminated` | Machine is destroyed |
| `failed` | `failed` | `failed` | Provisioning failed |

## Advanced Feature Mapping

### Provider Strategy Integration

| HostFactory Concept | Plugin Implementation | Benefits |
|---------------------|----------------------|----------|
| Single Provider | Provider Strategy Pattern | Multiple AWS handlers |
| Static Configuration | Dynamic Provider Selection | Runtime flexibility |
| Basic Error Handling | Circuit Breaker Pattern | Resilience |
| Simple Retry | Exponential Backoff | Reliability |

**Provider Strategy Configuration**:
```json
{
  "provider": {
    "active_provider": "aws-default",
    "selection_policy": "FIRST_AVAILABLE",
    "providers": [
      {
        "name": "aws-default",
        "type": "aws",
        "enabled": true,
        "config": {
          "handlers": {
            "types": {
              "ec2_fleet": "EC2Fleet",
              "spot_fleet": "SpotFleet",
              "asg": "ASG"
            }
          }
        }
      }
    ]
  }
}
```

### Storage Strategy Integration

| HostFactory Requirement | Plugin Implementation | Advantages |
|-------------------------|----------------------|------------|
| Request Persistence | Storage Strategy Pattern | Multiple backends |
| Simple File Storage | JSON Strategy | Easy debugging |
| Database Support | SQL Strategy | Scalability |
| State Recovery | Repository Pattern | Data consistency |

**Storage Strategy Options**:
- **JSON Strategy**: File-based storage for development
- **SQL Strategy**: Database storage for production
- **Repository Pattern**: Consistent data access interface

### Authentication and Security

| HostFactory Security | Plugin Implementation | AWS Integration |
|---------------------|----------------------|-----------------|
| User Authentication | AWS Profile/Role Support | IAM Integration |
| Resource Authorization | IAM Policy Enforcement | Fine-grained permissions |
| Audit Logging | Structured logging | CloudTrail integration |
| Secure Communication | HTTPS/TLS | AWS API security |

## Field Mapping Details

### Template Field Mapping

| HostFactory Field | Plugin Field | Type | Transformation |
|-------------------|--------------|------|----------------|
| `templateId` | `template_id` | String | Direct mapping |
| `maxNumber` | `max_number` | Integer | Direct mapping |
| `type` | `attributes.type` | Array | Format: `["String", "value"]` |
| `ncpus` | `attributes.ncpus` | Array | Format: `["Numeric", "value"]` |
| `nram` | `attributes.nram` | Array | Format: `["Numeric", "value"]` |

**Field Transformation Example**:
```python
# HostFactory format
{
  "templateId": "basic-template",
  "maxNumber": 10,
  "attributes": {
    "type": ["String", "X86_64"],
    "ncpus": ["Numeric", "2"]
  }
}

# Plugin internal format
{
  "template_id": "basic-template",
  "max_number": 10,
  "attributes": {
    "vm_type": "t3.medium",
    "cpu_count": 2,
    "memory_gb": 4
  }
}
```

### Machine Field Mapping

| HostFactory Field | Plugin Field | AWS Source | Notes |
|-------------------|--------------|------------|-------|
| `machineId` | `machine_id` | `InstanceId` | Direct mapping |
| `status` | `status` | `State.Name` | State translation |
| `ipAddress` | `ip_address` | `PrivateIpAddress` | Primary IP |
| `publicIpAddress` | `public_ip_address` | `PublicIpAddress` | If available |
| `launchTime` | `created_at` | `LaunchTime` | ISO format |

### Request Field Mapping

| HostFactory Field | Plugin Field | Storage | Description |
|-------------------|--------------|---------|-------------|
| `requestId` | `request_id` | Generated UUID | Unique identifier |
| `templateId` | `template_id` | From request | Template reference |
| `maxNumber` | `max_number` | From request | Machine count |
| `status` | `status` | State machine | Request state |
| `machines` | `machines` | Related entities | Machine list |

## Output Format Mapping

### Legacy Compatibility Mode

The plugin supports HostFactory's expected camelCase format:

```bash
# Default format (internal)
python src/run.py templates list
{
  "templates": [
    {
      "template_id": "basic-template",
      "max_number": 10
    }
  ]
}

# Legacy format (HostFactory compatible)
python src/run.py templates list --legacy
{
  "templates": [
    {
      "templateId": "basic-template",
      "maxNumber": 10
    }
  ]
}
```

### Output Format Options

| Format | HostFactory Usage | Plugin Command | Description |
|--------|-------------------|----------------|-------------|
| JSON | Default API format | `--format json` | Standard JSON output |
| Table | Human readable | `--format table` | Formatted table |
| YAML | Configuration | `--format yaml` | YAML format |
| List | Simple listing | `--format list` | Plain text list |

## Error Mapping

### Error Code Mapping

| HostFactory Error | Plugin Error | HTTP Status | Description |
|-------------------|--------------|-------------|-------------|
| Template Not Found | `TEMPLATE_NOT_FOUND` | 404 | Invalid template ID |
| Invalid Request | `INVALID_REQUEST` | 400 | Malformed request |
| Provisioning Failed | `PROVISIONING_FAILED` | 500 | Cloud provider error |
| Quota Exceeded | `QUOTA_EXCEEDED` | 429 | Resource limits |
| Authentication Failed | `AUTH_FAILED` | 401 | Invalid credentials |

### Error Response Format

```json
{
  "error": {
    "code": "TEMPLATE_NOT_FOUND",
    "message": "Template 'invalid-template' not found",
    "details": {
      "available_templates": ["basic-template", "advanced-template"],
      "request_id": "req-12345",
      "timestamp": "2024-01-15T10:30:00Z"
    }
  }
}
```

## Performance Mapping

### Response Time Mapping

| HostFactory Operation | Expected Time | Plugin Performance | Optimization |
|-----------------------|---------------|-------------------|--------------|
| `getAvailableTemplates` | < 30s | < 5s | Template caching |
| `requestMachines` | < 60s | < 30s | Async provisioning |
| `getRequestStatus` | < 10s | < 3s | Status caching |
| `requestReturnMachines` | < 60s | < 30s | Batch termination |

### Scalability Mapping

| HostFactory Limit | Plugin Support | Implementation |
|-------------------|----------------|----------------|
| 1000 templates | [IMPLEMENTED] Supported | JSON/SQL storage |
| 100 concurrent requests | [IMPLEMENTED] Supported | Async processing |
| 10000 machines/request | [IMPLEMENTED] Supported | Batch operations |
| 30 days history | [IMPLEMENTED] Supported | Persistent storage |

## Integration Patterns

### Shell Script Integration

```bash
# HostFactory calls
./scripts/getAvailableTemplates.sh

# Plugin delegation
"$(dirname "$0")/invoke_provider.sh" templates list "$@"

# Core execution
python src/run.py templates list --legacy --format json
```

### Configuration Integration

```json
{
  "hostfactory": {
    "compatibility_mode": true,
    "legacy_field_names": true,
    "timeout_seconds": 300,
    "retry_attempts": 3,
    "output_format": "json"
  }
}
```

### Monitoring Integration

| HostFactory Metric | Plugin Metric | Collection Method |
|---------------------|---------------|-------------------|
| Request Success Rate | `requests_success_rate` | Application metrics |
| Average Response Time | `response_time_avg` | Request timing |
| Active Machines | `machines_active_count` | State tracking |
| Error Rate | `error_rate` | Error counting |

## Extension Points

### Intelligent Attribute Generation

The plugin automatically generates HostFactory attributes with intelligent CPU and RAM specifications based on AWS instance types:

```json
{
  "templateId": "custom-template",
  "attributes": {
    "type": ["String", "X86_64"],
    "ncpus": ["Numeric", "4"],
    "nram": ["Numeric", "16384"]
  }
}
```

**Instance Type Mapping**:
The plugin includes built-in CPU and RAM mappings for common AWS instance types:

| Instance Type | vCPUs | RAM (MB) | Generated Attributes |
|---------------|-------|----------|---------------------|
| `t2.micro` | 1 | 1024 | `ncpus: ["Numeric", "1"], nram: ["Numeric", "1024"]` |
| `t2.small` | 1 | 2048 | `ncpus: ["Numeric", "1"], nram: ["Numeric", "2048"]` |
| `t2.medium` | 2 | 4096 | `ncpus: ["Numeric", "2"], nram: ["Numeric", "4096"]` |
| `t3.medium` | 2 | 4096 | `ncpus: ["Numeric", "2"], nram: ["Numeric", "4096"]` |
| `m5.large` | 2 | 8192 | `ncpus: ["Numeric", "2"], nram: ["Numeric", "8192"]` |
| `m5.xlarge` | 4 | 16384 | `ncpus: ["Numeric", "4"], nram: ["Numeric", "16384"]` |
| `c5.large` | 2 | 4096 | `ncpus: ["Numeric", "2"], nram: ["Numeric", "4096"]` |
| `c5.xlarge` | 4 | 8192 | `ncpus: ["Numeric", "4"], nram: ["Numeric", "8192"]` |
| `r5.large` | 2 | 16384 | `ncpus: ["Numeric", "2"], nram: ["Numeric", "16384"]` |
| `r5.xlarge` | 4 | 32768 | `ncpus: ["Numeric", "4"], nram: ["Numeric", "32768"]` |

**Automatic Detection**:
- Attributes are automatically generated based on the `instance_type` or `instanceType` field in templates
- Supports both snake_case (`instance_type`) and camelCase (`instanceType`) field names
- Falls back to `t2.micro` specifications (1 vCPU, 1024 MB RAM) for unknown instance types
- Always includes the standard `type: ["String", "X86_64"]` attribute

### Custom Attributes

The plugin also supports additional HostFactory custom attributes:

```json
{
  "templateId": "custom-template",
  "attributes": {
    "type": ["String", "X86_64"],
    "ncpus": ["Numeric", "4"],
    "nram": ["Numeric", "8192"],
    "custom_attribute": ["String", "custom_value"],
    "environment": ["String", "production"]
  }
}
```

### Provider Extensions

New providers can be added while maintaining HostFactory compatibility:

```json
{
  "providers": [
    {
      "name": "aws-primary",
      "type": "aws"
    },
    {
      "name": "azure-secondary", 
      "type": "azure"
    }
  ]
}
```

This comprehensive feature mapping ensures that all HostFactory capabilities are preserved and extended through the plugin's current architecture while maintaining full backward compatibility.

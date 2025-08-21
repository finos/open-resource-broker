# Native Spec API Reference

## Overview

Native AWS Spec support allows you to specify AWS API configurations directly in your templates using Jinja2 templating. This provides full access to AWS API capabilities while maintaining template flexibility.

## Template Fields Reference

### Core Native Spec Fields

#### `launch_template_spec`
- **Type**: `Dict[str, Any]`
- **Description**: Inline AWS LaunchTemplate specification
- **Mutually Exclusive With**: `launch_template_spec_file`
- **Supported APIs**: All (EC2Fleet, SpotFleet, AutoScaling, RunInstances)

```json
{
  "launch_template_spec": {
    "LaunchTemplateName": "lt-{{ request_id }}",
    "LaunchTemplateData": {
      "ImageId": "{{ image_id }}",
      "InstanceType": "{{ instance_type }}"
    }
  }
}
```

#### `launch_template_spec_file`
- **Type**: `str`
- **Description**: Path to LaunchTemplate spec file (relative to `spec_file_base_path`)
- **Mutually Exclusive With**: `launch_template_spec`
- **Supported APIs**: All (EC2Fleet, SpotFleet, AutoScaling, RunInstances)

```json
{
  "launch_template_spec_file": "examples/basic/launch-template-minimal.json"
}
```

#### `provider_api_spec`
- **Type**: `Dict[str, Any]`
- **Description**: Inline AWS provider API specification
- **Mutually Exclusive With**: `provider_api_spec_file`
- **Supported APIs**: EC2Fleet, SpotFleet, AutoScaling

```json
{
  "provider_api_spec": {
    "Type": "instant",
    "TargetCapacitySpecification": {
      "TotalTargetCapacity": "{{ requested_count }}"
    }
  }
}
```

#### `provider_api_spec_file`
- **Type**: `str`
- **Description**: Path to provider API spec file (relative to `spec_file_base_path`)
- **Mutually Exclusive With**: `provider_api_spec`
- **Supported APIs**: EC2Fleet, SpotFleet, AutoScaling

```json
{
  "provider_api_spec_file": "examples/basic/ec2fleet-instant.json"
}
```

### Field Validation Rules

1. **Mutual Exclusion**: Cannot specify both inline spec and spec file for the same type
2. **Provider API Compatibility**: `provider_api_spec` only applies to fleet-based APIs
3. **Template Variable Validation**: All Jinja2 variables must be resolvable
4. **AWS Schema Validation**: Specs must conform to AWS API schemas

## Template Variable Reference

### Standard Variables (Available in all templates)

#### Core Request Variables
- `{{ request_id }}`: Unique request identifier (UUID format)
  - **Type**: `str`
  - **Example**: `"req-123e4567-e89b-12d3-a456-426614174000"`

- `{{ requested_count }}`: Number of instances requested
  - **Type**: `int`
  - **Example**: `5`

- `{{ template_id }}`: Template identifier
  - **Type**: `str`
  - **Example**: `"web-server-template"`

#### Package Metadata Variables
- `{{ package_name }}`: Package name for resource tagging
  - **Type**: `str`
  - **Default**: `"open-hostfactory-plugin"`
  - **Example**: `"open-hostfactory-plugin"`

- `{{ package_version }}`: Package version for metadata
  - **Type**: `str`
  - **Default**: `"unknown"`
  - **Example**: `"1.0.0"`

### AWS-Specific Variables

#### Instance Configuration
- `{{ image_id }}`: AMI ID
  - **Type**: `str`
  - **Format**: `ami-xxxxxxxx`
  - **Example**: `"ami-0abcdef1234567890"`

- `{{ instance_type }}`: EC2 instance type
  - **Type**: `str`
  - **Example**: `"t3.micro"`

#### Networking Variables
- `{{ subnet_ids }}`: List of subnet IDs for multi-AZ deployment
  - **Type**: `List[str]`
  - **Example**: `["subnet-12345", "subnet-67890"]`

- `{{ security_group_ids }}`: List of security group IDs
  - **Type**: `List[str]`
  - **Example**: `["sg-12345", "sg-67890"]`

- `{{ key_name }}`: EC2 key pair name for SSH access
  - **Type**: `str`
  - **Example**: `"my-keypair"`

#### Launch Template Variables (when using launch templates)
- `{{ launch_template_id }}`: Launch template ID
  - **Type**: `str`
  - **Example**: `"lt-0abcdef1234567890"`

- `{{ launch_template_version }}`: Launch template version
  - **Type**: `str`
  - **Example**: `"$Latest"` or `"1"`

### Custom Variables

You can define custom variables in your template configuration that will be available in native specs:

```json
{
  "template_id": "custom-vars-example",
  "custom_variables": {
    "environment": "production",
    "project": "web-app",
    "cost_center": "engineering"
  },
  "provider_api_spec": {
    "TagSpecifications": [{
      "Tags": [
        {"Key": "Environment", "Value": "{{ environment }}"},
        {"Key": "Project", "Value": "{{ project }}"},
        {"Key": "CostCenter", "Value": "{{ cost_center }}"}
      ]
    }]
  }
}
```

## Provider API Support Matrix

| Provider API | Launch Template Spec | Provider API Spec | Native Spec Status | Legacy Fallback |
|--------------|---------------------|-------------------|-------------------|-----------------|
| **EC2Fleet** | Supported | Supported | Stable | Available |
| **SpotFleet** | Supported | Supported | Stable | Available |
| **AutoScaling** | Supported | Supported | Stable | Available |
| **RunInstances** | Supported | Not Applicable | Stable | Available |

### API-Specific Considerations

#### EC2Fleet
- Supports both `instant` and `maintain` fleet types
- `provider_api_spec` maps directly to `CreateFleet` API parameters
- Launch template ID/version automatically injected into `LaunchTemplateConfigs`

#### SpotFleet
- `provider_api_spec` maps to `RequestSpotFleet` API parameters
- Supports both `request` and `maintain` fleet types
- Launch specifications can be defined in launch template or inline

#### AutoScaling
- `provider_api_spec` maps to `CreateAutoScalingGroup` API parameters
- Launch template specification automatically configured
- Supports mixed instance policies and lifecycle hooks

#### RunInstances
- Only supports `launch_template_spec` (no provider API spec)
- Maps directly to `RunInstances` API parameters
- Simpler configuration for basic instance launches

## Jinja2 Template Features

### Supported Jinja2 Features

#### Variable Substitution
```json
{
  "InstanceType": "{{ instance_type }}",
  "ImageId": "{{ image_id }}"
}
```

#### Conditional Logic
```json
{
  "SpotPrice": "{{ spot_price if use_spot else '' }}"
}
```

#### Loops and Iteration
```json
{
  "SecurityGroups": [
    {% for sg_id in security_group_ids %}
    {"GroupId": "{{ sg_id }}"}{% if not loop.last %},{% endif %}
    {% endfor %}
  ]
}
```

#### Filters
```json
{
  "UserData": "{{ user_data_script | b64encode }}",
  "TotalCapacity": "{{ (requested_count * 1.2) | round | int }}"
}
```

#### Default Values
```json
{
  "InstanceType": "{{ instance_type | default('t3.micro') }}",
  "Environment": "{{ environment | default('production') }}"
}
```

### Built-in Filters

- `b64encode`: Base64 encode strings (useful for UserData)
- `round`: Round floating point numbers
- `int`: Convert to integer
- `default(value)`: Provide default value if variable is undefined
- `length`: Get length of lists/strings
- `join(separator)`: Join list elements with separator

## Error Handling

### Template Rendering Errors
- **Undefined Variable**: Template rendering fails if required variable is missing
- **Syntax Error**: Invalid Jinja2 syntax causes template parsing failure
- **Type Error**: Incompatible variable types cause rendering errors

### AWS API Validation Errors
- **Schema Validation**: Invalid AWS API parameters are rejected
- **Resource Limits**: AWS service limits may cause deployment failures
- **Permission Errors**: Insufficient IAM permissions cause API failures

### Fallback Behavior
When native spec processing fails:
1. Error is logged with detailed context
2. System falls back to legacy template processing (if enabled)
3. Request continues with legacy configuration
4. Monitoring alerts are triggered for investigation

## Performance Considerations

### Template Caching
- Parsed templates are cached to improve performance
- Cache size configurable via `template_cache_size` setting
- Cache invalidation occurs on template file changes

### Rendering Performance
- Simple variable substitution: ~1ms per template
- Complex loops and conditionals: ~5-10ms per template
- File-based specs: Additional file I/O overhead (~2-5ms)

### Best Practices for Performance
1. Use inline specs for frequently used templates
2. Minimize complex Jinja2 expressions
3. Pre-compute values when possible
4. Use template caching in production environments

## Security Considerations

### Template Security
- No arbitrary code execution (Jinja2 sandboxed environment)
- Variable injection is escaped and validated
- File access restricted to configured base paths

### AWS Resource Security
- All AWS resources created with appropriate tags
- IAM permissions required for resource creation
- Security groups and network ACLs enforced
- Encryption enabled by default where supported

## Migration from Legacy Templates

### Compatibility Mode
Native specs can run alongside legacy templates:
- `native_spec.enabled = true` enables native spec processing
- Legacy templates continue to work unchanged
- Gradual migration supported

### Migration Strategy
1. Enable native specs in parallel
2. Create native spec versions of critical templates
3. Test native specs in non-production environments
4. Migrate production templates
5. Disable legacy processing

See [Migration Guide](../migration/legacy-to-native-spec.md) for detailed instructions.
